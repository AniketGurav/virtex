r"""
Parts of this class are adopted from several of my past projects:

- `https://github.com/kdexd/probnmn-clevr/blob/master/probnmn/config.py`_
- `https://github.com/nocaps-org/updown-baseline/blob/master/updown/config.py`_
"""
import re
from typing import Any, List, Optional

from loguru import logger
from yacs.config import CfgNode as CN
import viswsl.utils.distributed as dist


class Config(object):
    r"""
    This class provides package-wide configuration management. It is a
    nested dict-like structure with nested keys accessible as attributes. It
    contains sensible default values, which can be modified by (first) a YAML
    file and (second) a list of attributes and values.

    Note
    ----
    The instantiated object is "immutable" - any modification is prohibited.
    You must override required parameter values either through ``config_file``
    or ``override_list``.

    Parameters
    ----------
    config_file: str
        Path to a YAML file containing configuration parameters to override.
    config_override: List[Any], optional (default= [])
        A list of sequential attributes and values of parameters to override.
        This happens after overriding from YAML file.

    Examples
    --------
    Let a YAML file named "config.yaml" specify these parameters to override::

        RANDOM_SEED: 42
        OPTIM:
          WEIGHT_DECAY: 1e-2

    >>> _C = Config("config.yaml", ["OPTIM.WEIGHT_DECAY", 1e-4])
    >>> _C.RANDOM_SEED  # default: 0
    42
    >>> _C.OPTIM.WEIGHT_DECAY  # default: 1e-3
    1e-4

    Attributes
    ----------
    RANDOM_SEED: 0
        Random seed for NumPy and PyTorch, important for reproducibility.
    __________

    DATA:
        Collection of required data paths for training and evaluation. All
        these are assumed to be relative to project root directory. If
        elsewhere, symlinking is recommended.

    DATA.VOCABULARY: "data/coco_vocabulary.vocab"
        Path to a ``**.vocab`` file containing tokens. This file is used to
        instantiate :class:`~viswsl.data.vocabulary.SentencePieceVocabulary`.
    DATA.TOKENIZER: "data/coco_vocabulary.model"
        Path to a ``**.model`` file containing tokenizer model trained by
        `sentencepiece <https://www.github.com/google/sentencepiece>`_, used
        to instantiate :class:`~viswsl.data.tokenizer.SentencePieceTokenizer`.
    DATA.TRAIN_LMDB: data/serialized/coco_train2017.lmdb
        Path to an LMDB file containing training examples serialized as
        ``(image: np.ndarray, captions: List[str])``.
    DATA.VAL_LMDB: data/serialized/coco_val2017.lmdb
        Path to an LMDB file containing validation examples serialized as
        ``(image: np.ndarray, captions: List[str])``.
    DATA.NORMALIZE_IMAGE: True
        Whether to normalize the image by RGB color mean and variance.
    DATA.MAX_CAPTION_LENGTH: 30
        Maximum length of captions as input to the textual stream. Captions
        longer than this will be truncated to maximum length.
    __________

    MODEL:

    MODEL.VISUAL:
        Parameters defining the architecture of the visual stream.
    MODEL.VISUAL.NAME: "torchvision::resnet50"
        Name of the visual stream model. Torchvision models supported for now.
    MODEL.VISUAL.NORM_LAYER: GN
        One of ``["BN", "GN"]``. Instance Norm and Layer Norm are special cases
        of Group Norm.
    MODEL.VISUAL.NUM_GROUPS: 32
        Number of groups for Group Norm. Ignored if ``MODEL.VISUAL.NORM_LAYER``
        is ``BN``.
    MODEL.VISUAL.PRETRAINED:
        Whether to initialize model from ImageNet pre-trained weights.
    _____

    MODEL.TEXTUAL:
        Parameters defining the architecture of the textual stream.
    MODEL.TEXTUAL.NUM_LAYERS: 6
        Number of layers in the transformer encoder.
    __________

    OPTIM:
        Optimization hyper-parameters, mostly relevant during training.

    OPTIM.OPTIMIZER_NAME: adamw
        One of ``["sgd", "adam", "adamw"]``.
    OPTIM.NUM_ITERATIONS: 1000000
        Number of iterations to train for, batches are randomly sampled.
    OPTIM.BATCH_SIZE_PER_GPU: 64
        Batch size per GPU (or just CPU) during training and evaluation.
    OPTIM.BATCH_SIZE_MULTIPLIER: 1
        Number of batches to use for accumulating gradients before taking
        optimizer step. Useful to simulate large batch sizes.

    .. note::
        At the start of training, two config parameters will be created:
            1. ``BATCH_SIZE_PER_ITER = BATCH_SIZE_PER_GPU * num_gpus``
            2. ``TOTAL_BATCH_SIZE = BATCH_SIZE_PER_ITER * BATCH_SIZE_MULTIPLIER``
        These are just for reference and should not be used anywhere.

    OPTIM.LR: 1e-5
        Initial learning rate for optimizer. This linearly decays to zero till
        the end of training.
    OPTIM.WARMUP_STEPS: 10000
        Number of steps to perform LR warmup. Learning rate goes linearly from
        0 to ``OPTIM.LR`` for ``OPTIM.WARMUP_STEPS`` steps. A good rule of
        thumb is to set it as ``(2 / 1 - beta2)`` for Adam-like optimizers, or
        5-10% of total number of iterations.
    OPTIM.WEIGHT_DECAY: 1e-4
        Weight decay co-efficient for optimizer.
    OPTIM.SGD_MOMENTUM: 0.9
        Value for momentum co-efficient, only used when ``OPTIM.OPTIMIZER_NAME``
        is ``sgd``, else ignored.
    OPTIM.SGD_NESTEROV: True
        Whether to use Nesterive accelerated gradient, only used when
        ``OPTIM.OPTIMIZER_NAME`` is ``sgd``, else ignored.
    OPTIM.CLIP_GRAD_NORM: 10
        Threshold to clamp gradients for avoiding exploding gradients.
    """

    def __init__(
        self, config_file: Optional[str] = None, override_list: List[Any] = []
    ):
        _C = CN()
        _C.RANDOM_SEED = 0
        _C.FP16_OPT = 0

        _C.DATA = CN()
        _C.DATA.VOCABULARY = "data/coco_vocabulary.vocab"
        _C.DATA.TOKENIZER = "data/coco_vocabulary.model"
        _C.DATA.TRAIN_LMDB = "data/serialized/coco_train2017.lmdb"
        _C.DATA.VAL_LMDB = "data/serialized/coco_val2017.lmdb"
        _C.DATA.NORMALIZE_IMAGE = True
        _C.DATA.IMAGE_RESIZE_SIZE = 256
        _C.DATA.IMAGE_CROP_SIZE = 224
        _C.DATA.MAX_CAPTION_LENGTH = 30

        _C.DATA.IMAGE = CN()
        _C.DATA.IMAGE.RESIZE_SIZE = 256
        _C.DATA.IMAGE.CROP_SIZE = 224
        _C.DATA.IMAGE.COLOR_NORMALIZE = True
        _C.DATA.IMAGE.RANDOM_FLIP = True
        _C.DATA.IMAGE.PHOTOMETRIC_AUG = True

        _C.PRETEXT = CN()
        _C.PRETEXT.WORD_MASKING = CN()
        _C.PRETEXT.WORD_MASKING.MASK_PROPORTION = 0.15
        _C.PRETEXT.WORD_MASKING.MASK_PROBABILITY = 0.85
        _C.PRETEXT.WORD_MASKING.REPLACE_PROBABILITY = 0.10

        _C.PRETEXT.MOCO = CN()
        _C.PRETEXT.MOCO.FEATURE_SIZE = 128
        _C.PRETEXT.MOCO.MOMENTUM = 0.999
        _C.PRETEXT.MOCO.QUEUE_SIZE = 4096
        _C.PRETEXT.MOCO.TEMPERATURE = 0.07

        _C.MODEL = CN()
        _C.MODEL.NAME = "word_masking"

        _C.MODEL.VISUAL = CN()
        _C.MODEL.VISUAL.NAME = "torchvision::resnet50"
        _C.MODEL.VISUAL.FEATURE_SIZE = 2048
        _C.MODEL.VISUAL.NORM_LAYER = "GN"
        _C.MODEL.VISUAL.NUM_GROUPS = 32
        _C.MODEL.VISUAL.PRETRAINED = False

        _C.MODEL.TEXTUAL = CN()
        _C.MODEL.TEXTUAL.NAME = "postnorm_gelu"
        _C.MODEL.TEXTUAL.HIDDEN_SIZE = 512
        _C.MODEL.TEXTUAL.ATTENTION_HEADS = 8
        _C.MODEL.TEXTUAL.FEEDFORWARD_SIZE = 2048
        _C.MODEL.TEXTUAL.NUM_LAYERS = 6
        _C.MODEL.TEXTUAL.DROPOUT = 0.1

        _C.MODEL.FUSION = CN()
        _C.MODEL.FUSION.NAME = "multihead"
        _C.MODEL.FUSION.PROJECTION_SIZE = 512
        _C.MODEL.FUSION.ATTENTION_HEADS = 8
        _C.MODEL.FUSION.DROPOUT = 0.1

        _C.OPTIM = CN()
        _C.OPTIM.NUM_ITERATIONS = 1000000
        _C.OPTIM.OPTIMIZER_NAME = "adamw"
        _C.OPTIM.WEIGHT_DECAY = 1e-4
        _C.OPTIM.NO_DECAY = [".bn", ".norm", ".bias"]
        _C.OPTIM.CLIP_GRAD_NORM = 10

        _C.OPTIM.ADAM_BETA1 = 0.9
        _C.OPTIM.ADAM_BETA2 = 0.98
        _C.OPTIM.SGD_MOMENTUM = 0.9
        _C.OPTIM.SGD_NESTEROV = True
        _C.OPTIM.USE_LOOKAHEAD = False
        _C.OPTIM.LOOKAHEAD_STEPS = 5
        _C.OPTIM.LOOKAHEAD_ALPHA = 0.5

        _C.OPTIM.BATCH_SIZE_PER_GPU = 64
        _C.OPTIM.BATCH_SIZE_MULTIPLIER = 1

        _C.OPTIM.VISUAL_LR = 1e-3
        _C.OPTIM.LR = 1e-4
        _C.OPTIM.WARMUP_STEPS = 10000
        _C.OPTIM.LR_DECAY_NAME = "cosine"

        _C.DOWNSTREAM = CN()
        _C.DOWNSTREAM.VOC07_CLF = CN()
        _C.DOWNSTREAM.VOC07_CLF.DATA_ROOT = "data/VOC2007"
        _C.DOWNSTREAM.VOC07_CLF.BATCH_SIZE = 64
        _C.DOWNSTREAM.VOC07_CLF.LAYER_NAMES = ["layer3", "layer4"]
        _C.DOWNSTREAM.VOC07_CLF.SVM_COSTS = [0.01, 0.1, 1.0, 10.0]

        _C.DOWNSTREAM.LVIS = CN()
        _C.DOWNSTREAM.LVIS.D2_CONFIG = "configs/lvis_d2.yaml"
        _C.DOWNSTREAM.LVIS.NORM_LAYER = "FrozenBN"

        # Placeholders, set these values after merging from file.
        _C.OPTIM.BATCH_SIZE_PER_ITER = 0
        _C.OPTIM.TOTAL_BATCH_SIZE = 0

        # Override parameter values from YAML file first, then from override
        # list, then add derived params.
        self._C = _C
        if config_file is not None:
            self._C.merge_from_file(config_file)
        self._C.merge_from_list(override_list)

        self.add_derived_params()

        # Make an instantiated object of this class immutable.
        self._C.freeze()

    def dump(self, file_path: str):
        r"""Save config at the specified file path.

        Parameters
        ----------
        file_path: str
            (YAML) path to save config at.
        """
        self._C.dump(stream=open(file_path, "w"))

    def add_derived_params(self):
        r"""Add parameters with values derived from existing parameters."""
        # ---------------------------------------------------------------------
        # Set total batch size accounting for multiple-GPUs and multiplier.
        # These are usually not used anywhere, adding for better reproducibility.
        self._C.OPTIM.BATCH_SIZE_PER_ITER = (
            self._C.OPTIM.BATCH_SIZE_PER_GPU * dist.get_world_size()
        )
        self._C.OPTIM.TOTAL_BATCH_SIZE = (
            self._C.OPTIM.BATCH_SIZE_PER_ITER * self._C.OPTIM.BATCH_SIZE_MULTIPLIER
        )
        # ---------------------------------------------------------------------

        if self._C.FP16_OPT > 0 and "gelu" in self._C.MODEL.TEXTUAL.NAME:
            logger.warning("Cannot use GELU with FP16 precision, changing to RELU.")
            self._C.MODEL.TEXTUAL.NAME.replace("gelu", "relu")

        # ---------------------------------------------------------------------
        # Set textual stream architecture if specified in string.
        # For example: "prenorm_gelu::L6_H768_A12_F3072":
        #     L = layers, H = hidden_size, A = attention_heads, F= feedforward_size
        tstream_name_parts = self._C.MODEL.TEXTUAL.NAME.split("::")[-1].split("_")
        for name_part in tstream_name_parts:
            if name_part[0] == "L":
                self._C.MODEL.TEXTUAL.NUM_LAYERS = int(name_part[1:])
            elif name_part[0] == "H":
                self._C.MODEL.TEXTUAL.HIDDEN_SIZE = int(name_part[1:])
            elif name_part[0] == "A":
                self._C.MODEL.TEXTUAL.ATTENTION_HEADS = int(name_part[1:])
            elif name_part[0] == "F":
                self._C.MODEL.TEXTUAL.FEEDFORWARD_SIZE = int(name_part[1:])
        # ---------------------------------------------------------------------

        # ---------------------------------------------------------------------
        # For simplicity, set size and heads for fusion to be same as transformer.
        # This might be temporary, can possibly remove it later.
        if (
            self._C.MODEL.FUSION.PROJECTION_SIZE != self._C.MODEL.TEXTUAL.HIDDEN_SIZE
            or self._C.MODEL.FUSION.ATTENTION_HEADS
            != self._C.MODEL.TEXTUAL.ATTENTION_HEADS
        ):
            logger.warning("Setting hyperparams for fusion to be same as textual.")

        self._C.MODEL.FUSION.PROJECTION_SIZE = self._C.MODEL.TEXTUAL.HIDDEN_SIZE
        self._C.MODEL.FUSION.ATTENTION_HEADS = self._C.MODEL.TEXTUAL.ATTENTION_HEADS
        # ---------------------------------------------------------------------

    def __getattr__(self, attr: str):
        return self._C.__getattr__(attr)

    def __str__(self):
        common_string: str = str(CN({"RANDOM_SEED": self._C.RANDOM_SEED})) + "\n"
        common_string: str = str(CN({"FP16_OPT": self._C.FP16_OPT})) + "\n"
        common_string += str(CN({"DATA": self._C.DATA})) + "\n"
        common_string += str(CN({"PRETEXT": self._C.PRETEXT})) + "\n"
        common_string += str(CN({"MODEL": self._C.MODEL})) + "\n"
        common_string += str(CN({"OPTIM": self._C.OPTIM})) + "\n"
        common_string += str(CN({"DOWNSTREAM": self._C.DOWNSTREAM})) + "\n"

        return common_string

    def __repr__(self):
        return self._C.__repr__()
