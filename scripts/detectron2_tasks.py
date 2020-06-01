"""
Finetune a pre-trained model on a downstream task, one of those available in
Detectron2. Optionally use gradient checkpointing for saving memory.
Supported downstream:
  - LVIS Instance Segmentation
  - Pascal VOC 2007+12 Object Detection

Reference: https://github.com/facebookresearch/detectron2/blob/master/tools/train_net.py
Thanks to the developers of Detectron2!
"""
import argparse
import os
import random
import re
import time
from typing import Any, Dict, Union

import numpy as np
import torch
from torch import nn
from apex.parallel import DistributedDataParallel as ApexDDP

import detectron2 as d2
from detectron2.checkpoint import DetectionCheckpointer
from detectron2.config import global_cfg
from detectron2.engine import SimpleTrainer, DefaultTrainer, default_setup, EvalHook
from detectron2.evaluation import (
    LVISEvaluator,
    PascalVOCDetectionEvaluator,
    COCOEvaluator,
)

from viswsl.config import Config
from viswsl.factories import PretrainingModelFactory
import viswsl.utils.distributed as dist


parser = argparse.ArgumentParser(
    description="""
    Finetune our pre-trained model on Detectron2 supported tasks.
"""
)
# fmt: off
parser.add_argument(
    "--task", required=True, choices=["lvis", "voc", "coco"],
)
parser.add_argument(
    "--pretext-config", required=True,
    help="""Path to a config file used to pre-train the model whose checkpoint
    will be loaded."""
)
parser.add_argument(
    "--d2-config", required=True,
    help="Path to a detectron2 config for downstream task finetuning."
)
parser.add_argument(
    "--d2-config-override", nargs="*", default=[],
    help="""Key-value pairs from Detectron2 config to override from file.
    Some keys will be ignored because they are set from other args:
    [DATALOADER.NUM_WORKERS, SOLVER.EVAL_PERIOD, SOLVER.CHECKPOINT_PERIOD,
    TEST.EVAL_PERIOD, OUTPUT_DIR]""",
)
parser.add_argument(
    "--cpu-workers", type=int, default=2, help="Number of CPU workers."
)
parser.add_argument(
    "--dist-backend", default="nccl", choices=["nccl", "gloo"],
    help="torch.distributed backend for distributed training.",
)
parser.add_argument(
    "--slurm", action="store_true",
    help="""Whether using SLURM for launching distributed training processes.
    Set `$MASTER_PORT` env variable externally for distributed process group
    communication."""
)

parser.add_argument_group("Checkpointing and Logging")
parser.add_argument(
    "--weight-init", choices=["random", "imagenet", "checkpoint"],
    default="checkpoint", help="""How to initialize weights: 'random' initializes
    all weights randomly, 'imagenet' initializes backbone weights from torchvision
    model zoo, and 'checkpoint' loads state dict from `--checkpoint-path`."""
)
parser.add_argument(
    "--resume", action="store_true", help="""Specify this flag when resuming
    training from a checkpoint saved by Detectron2."""
)
parser.add_argument(
    "--checkpoint-path",
    help="""Path to load checkpoint and run downstream task evaluation. The
    name of checkpoint file is required to be `model_*.pth`, where * is
    iteration number from which the checkpoint was serialized."""
)
parser.add_argument(
    "--serialization-dir", required=True,
    help="Path to a directory to save checkpoints and log stats."
)
parser.add_argument(
    "--checkpoint-every", type=int, default=2000,
    help="Serialize model to a checkpoint after every these many iterations.",
)
# fmt: on


def build_detectron2_config(_C: Config, _A: argparse.Namespace):
    r"""Build detectron2 config based on our pre-training config and args."""
    _D2C = d2.config.get_cfg()

    # Override some default values based on our config file.
    _D2C.merge_from_file(_A.d2_config)
    _D2C.merge_from_list(_A.d2_config_override)

    # Set workers etc. from args.
    _D2C.DATALOADER.NUM_WORKERS = _A.cpu_workers
    _D2C.SOLVER.EVAL_PERIOD = _A.checkpoint_every
    _D2C.SOLVER.CHECKPOINT_PERIOD = _A.checkpoint_every
    _D2C.TEST.EVAL_PERIOD = _A.checkpoint_every
    _D2C.OUTPUT_DIR = _A.serialization_dir

    # Set ResNet depth to override in Detectron2's config.
    _D2C.MODEL.RESNETS.DEPTH = int(
        re.search(r"resnet(\d+)", _C.MODEL.VISUAL.NAME).group(1)
        if "torchvision" in _C.MODEL.VISUAL.NAME
        else re.search(r"_R_(\d+)", _C.MODEL.VISUAL.NAME).group(1)
        if "detectron2" in _C.MODEL.VISUAL.NAME
        else 0
    )

    # Always turn on gradient checkpointing. MoCo models were trained on V100s. They
    # don't fit two images per GPU as-is on smaller GPUs.
    global_cfg.GRADIENT_CHECKPOINT = True

    # Task specific adjustments.
    if _A.task == "lvis":
        if _A.weight_init == "imagenet":
            # If using LVIS and ImageNet backbone, use FrozenBN and no BN in FPN.
            _D2C.MODEL.RESNETS.NORM = "FrozenBN"
            _D2C.MODEL.FPN.NORM = ""
        global_cfg.GRADIENT_CHECKPOINT = True
    elif _A.task in {"voc_moco", "voc_pirl"}:
        # Need gradient checkpointing for non-FPN backbones to fit two images
        # per GPU. Add it in GLOBAL config because it is a custon hack not in D2.
        global_cfg.GRADIENT_CHECKPOINT = True

    if _A.task == "voc":
        global_cfg.SYNCBN_AFTER_RES5 = _D2C.MODEL.RESNETS.NORM
    return _D2C


class LazyEvalHook(EvalHook):
    r"""Extension of detectron2's ``EvalHook``: start evaluation after few iters."""

    def __init__(self, start_after, eval_period, eval_function):
        self._start_after = start_after
        super().__init__(eval_period, eval_function)

    def after_step(self):
        next_iter = self.trainer.iter + 1
        if next_iter >= self._start_after:
            super().after_step()


class DownstreamTrainer(DefaultTrainer):
    r"""
    Extension of detectron2's ``DefaultTrainer``: custom evaluator and hooks.

    Parameters
    ----------
    cfg: detectron2.config.CfgNode
        Detectron2 config object containing all config params.
    weights: Union[str, Dict[str, Any]]
        Weights to load in the initialized model. If ``str``, then we assume path
        to a checkpoint, or if a ``dict``, we assume a state dict. This will be
        an ``str`` only if we resume training from a Detectron2 checkpoint.
    """

    def __init__(self, cfg, weights: Union[str, Dict[str, Any]]):
        self.start_iter = 0
        self.max_iter = cfg.SOLVER.MAX_ITER
        self.cfg = cfg

        # We do not make any super call here and implement `__init__` from
        #  `DefaultTrainer`: we need to initialize mixed precision model before
        # wrapping to DDP, so we need to do it this way.
        model = self.build_model(cfg)
        optimizer = self.build_optimizer(cfg, model)
        data_loader = self.build_train_loader(cfg)
        scheduler = self.build_lr_scheduler(cfg, optimizer)

        # Load pre-trained weights before wrapping to DDP because `ApexDDP` has
        # some weird issue with `DetectionCheckpointer`.
        # fmt: off
        if isinstance(weights, str):
            # weights are ``str`` means ImageNet init or resume training.
            self.start_iter = (
                DetectionCheckpointer(
                    model, optimizer=optimizer, scheduler=scheduler
                ).resume_or_load(weights, resume=True).get("iteration", -1) + 1
            )
        elif isinstance(weights, dict):
            # weights are a state dict means our pretext init.
            DetectionCheckpointer(model)._load_model(weights)
        # fmt: on

        # Enable distributed training if we have multiple GPUs. Use Apex DDP for
        # non-FPN backbones because its `delay_allreduce` functionality helps with
        # gradient checkpointing.
        if dist.get_world_size() > 1:
            if global_cfg.get("GRADIENT_CHECKPOINT", False):
                model = ApexDDP(model, delay_allreduce=True)
            else:
                model = nn.parallel.DistributedDataParallel(
                    model, device_ids=[dist.get_rank()], broadcast_buffers=False
                )

        # Call `__init__` from grandparent class: `SimpleTrainer`.
        SimpleTrainer.__init__(self, model, data_loader, optimizer)

        self.scheduler = scheduler
        self.checkpointer = DetectionCheckpointer(
            model, cfg.OUTPUT_DIR, optimizer=optimizer, scheduler=self.scheduler
        )
        self.register_hooks(self.build_hooks())

    @classmethod
    def build_evaluator(cls, cfg, dataset_name, output_folder=None):
        if output_folder is None:
            output_folder = os.path.join(cfg.OUTPUT_DIR, "inference")
        evaluator_list = []
        evaluator_type = d2.data.MetadataCatalog.get(dataset_name).evaluator_type
        if evaluator_type == "pascal_voc":
            return PascalVOCDetectionEvaluator(dataset_name)
        elif evaluator_type == "coco":
            return COCOEvaluator(dataset_name, cfg, True, output_folder)
        elif evaluator_type == "lvis":
            return LVISEvaluator(dataset_name, cfg, True, output_folder)

    def build_hooks(self):
        __C = self.cfg.clone()

        def _eval():
            # Function for ``LazyEvalHook``.
            self._last_eval_results = self.test(self.cfg, self.model)
            return self._last_eval_results

        # Do iteration timing, LR scheduling, checkpointing, logging etc.
        ret = [
            d2.engine.hooks.IterationTimer(),
            d2.engine.hooks.LRScheduler(self.optimizer, self.scheduler),
            d2.engine.hooks.PeriodicCheckpointer(
                self.checkpointer, __C.SOLVER.CHECKPOINT_PERIOD
            ),
            LazyEvalHook(__C.SOLVER.STEPS[0], __C.TEST.EVAL_PERIOD, _eval),
            d2.engine.hooks.PeriodicWriter(self.build_writers()),
        ]
        # We need checkpointer and writer only for master process.
        return ret if dist.is_master_process() else [ret[0], ret[1], ret[3]]

    def run_step(self):
        r"""Extend ``run_step`` from ``SimpleTrainer``: support mixed precision."""

        torch.cuda.empty_cache()
        # All this is similar to super class method.
        start = time.perf_counter()
        data = next(self._data_loader_iter)
        data_time = time.perf_counter() - start

        loss_dict = self.model(data)
        losses = sum(loss_dict.values())
        self._detect_anomaly(losses, loss_dict)

        metrics_dict = loss_dict
        metrics_dict["data_time"] = data_time
        self._write_metrics(metrics_dict)

        self.optimizer.zero_grad()
        losses.backward()
        self.optimizer.step()


if __name__ == "__main__":

    _A = parser.parse_args()
    config_override = (
        ["MODEL.VISUAL.PRETRAINED", True] if _A.weight_init == "imagenet" else []
    )

    # Set up distributed environment - we use our `dist` utilities instead of
    # detectron2's utilities because it's easier with slurm.
    device_id = dist.init_distributed_env(_A.dist_backend) if _A.slurm else -1
    device = torch.device(f"cuda:{device_id}" if device_id != -1 else "cpu")
    if device_id != -1:
        local_pg = list(range(dist.get_world_size()))
        d2.utils.comm._LOCAL_PROCESS_GROUP = torch.distributed.new_group(local_pg)

    # Create config with default values, then override from config file.
    # This is our config, not Detectron2 config.
    _C = Config(_A.pretext_config, config_override)

    # We use `default_setup` from detectron2 to do some common setup, such as
    # logging, setting up serialization etc. For more info, look into source.
    _D2C = build_detectron2_config(_C, _A)
    default_setup(_D2C, _A)
    print(global_cfg)

    # We override the random seeds set by Detectron2 and set the same seed
    # for all workers to completely control randomness.
    # For reproducibility - refer https://pytorch.org/docs/stable/notes/randomness.html
    random.seed(_D2C.SEED)
    np.random.seed(_D2C.SEED)
    torch.manual_seed(_D2C.SEED)
    torch.backends.cudnn.benchmark = False
    torch.backends.cudnn.deterministic = True

    # Prepare weights to pass in instantiation call of trainer.
    if _A.weight_init == "checkpoint":
        if _A.resume:
            # If resuming training, let Detectron2 load weights by providing path.
            model = None
            weights = _A.checkpoint_path
        else:
            # Load backbone weights from our pre-trained checkpoint.
            model = PretrainingModelFactory.from_config(_C)
            model.load_state_dict(torch.load(_A.checkpoint_path, map_location="cpu"))
            weights = model.visual.detectron2_backbone_state_dict()
    else:
        # If random or imagenet init, just load weights after initializing model.
        model = PretrainingModelFactory.from_config(_C)
        weights = model.visual.detectron2_backbone_state_dict()

    # Back up pretext config and model checkpoint (if provided).
    _C.dump(os.path.join(_A.serialization_dir, "pretext_config.yml"))
    if _A.weight_init == "checkpoint" and not _A.resume:
        torch.save(
            model.state_dict(),
            os.path.join(_A.serialization_dir, "pretext_model.pth"),
        )

    del model

    # Instantiate trainer and start training.
    trainer = DownstreamTrainer(_D2C, weights)
    trainer.train()
