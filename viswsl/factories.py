from functools import partial
from typing import Any, Callable, Dict, Iterable, List, Optional

import albumentations as alb
from torch import nn, optim

from viswsl.config import Config
import viswsl.data as vdata
from viswsl.data import transforms as T
from viswsl.data.tokenizer import SentencePieceBPETokenizer
import viswsl.models as vmodels
from viswsl.modules import visual_stream as vs, textual_stream as ts
from viswsl.optim import Lookahead, lr_scheduler


class Factory(object):

    PRODUCTS: Dict[str, Any] = {}

    def __init__(self):
        raise ValueError(
            f"""Cannot instantiate {self.__class__.__name__} object, use
            `create` classmethod to create a product from this factory.
            """
        )

    @property
    def products(self) -> List[str]:
        return list(self.PRODUCTS.keys())

    @classmethod
    def create(cls, name: str, *args, **kwargs) -> Any:
        if name not in cls.PRODUCTS:
            raise KeyError(f"{cls.__class__.__name__} cannot create {name}.")

        return cls.PRODUCTS[name](*args, **kwargs)

    @classmethod
    def from_config(cls, config: Config) -> Any:
        raise NotImplementedError


class TokenizerFactory(Factory):

    PRODUCTS = {"SentencePieceBPETokenizer": SentencePieceBPETokenizer}

    @classmethod
    def from_config(cls, config: Config) -> SentencePieceBPETokenizer:
        _C = config

        tokenizer = cls.create(
            "SentencePieceBPETokenizer",
            vocab_path=_C.DATA.TOKENIZER_VOCAB,
            model_path=_C.DATA.TOKENIZER_MODEL,
        )
        return tokenizer


class ImageTransformsFactory(Factory):

    # fmt: off
    PRODUCTS = {
        # Input resize transforms: whenever selected, these are always applied.
        # These transforms require one position argument: image dimension.
        "random_resized_crop": partial(
            T.RandomResizedSquareCrop, scale=(0.2, 1.0), ratio=(0.75, 1.333), p=1.0
        ),
        "smallest_max_size": partial(alb.SmallestMaxSize, p=1.0),
        "center_crop": partial(T.CenterSquareCrop, p=1.0),

        # Data augmentations: whenever selected, these are applied with 50%
        # probability, except ColorJitter which is always applied.
        "horizontal_flip": partial(T.HorizontalFlip, p=0.5),
        "color_jitter_mild": partial(
            T.ColorJitter, brightness=0.2, contrast=0.2, saturation=0.2, hue=0.2, p=1.0
        ),
        "color_jitter_heavy": partial(
            T.ColorJitter, brightness=0.4, contrast=0.4, saturation=0.4, hue=0.4, p=1.0
        ),
        "lighting_noise": partial(T.LightingNoise, alpha=0.1, p=0.5),
        "gaussian_blur": partial(alb.GaussianBlur, blur_limit=7, p=0.5),

        # Color normalization: whenever selected, always applied.
        "normalize": partial(
            alb.Normalize, mean=T.IMAGENET_COLOR_MEAN, std=T.IMAGENET_COLOR_STD, p=1.0
        ),
    }
    # fmt: on

    @classmethod
    def from_config(cls, config: Config):
        r"""Augmentations cannot be created from config, only :meth:`create`."""
        raise NotImplementedError


class PretextDatasetFactory(Factory):

    PRODUCTS = {
        "word_masking": vdata.WordMaskingPretextDataset,
        "captioning": vdata.CaptioningPretextDataset,
        "bicaptioning": vdata.CaptioningPretextDataset,
        "token_classification": vdata.CaptioningPretextDataset,
        "instance_classification": vdata.InstanceClassificationDataset,
    }

    @classmethod
    def from_config(
        cls,
        config: Config,
        tokenizer: Optional[SentencePieceBPETokenizer] = None,
        split: str = "train",  # one of {"train", "val"}
    ):
        _C = config
        tokenizer = tokenizer or TokenizerFactory.from_config(_C)

        # Add model specific kwargs. Refer call signatures of specific datasets.
        # TODO (kd): InstanceClassificationDataset does not accept most of the
        # args. Make the API more consistent.
        if _C.MODEL.NAME != "instance_classification":
            kwargs = {
                "lmdb_path": _C.DATA.VAL_LMDB
                if split == "val"
                else _C.DATA.TRAIN_LMDB,
                "tokenizer": tokenizer,
                "max_caption_length": _C.DATA.MAX_CAPTION_LENGTH,
                "use_single_caption": _C.DATA.USE_SINGLE_CAPTION,
                "percentage": _C.DATA.USE_PERCENTAGE if split == "train" else 100.0,
            }
            if _C.MODEL.NAME == "word_masking":
                kwargs.update(
                    mask_proportion=_C.PRETEXT.WORD_MASKING.MASK_PROPORTION,
                    mask_probability=_C.PRETEXT.WORD_MASKING.MASK_PROBABILITY,
                    replace_probability=_C.PRETEXT.WORD_MASKING.REPLACE_PROBABILITY,
                )
        else:
            # TODO: add `root` argument after adding to config.
            kwargs = {"split": split}

        image_transform_names: List[str] = list(
            _C.DATA.IMAGE_TRANSFORM_TRAIN
            if split == "train"
            else _C.DATA.IMAGE_TRANSFORM_VAL
        )
        # Create a list of image transformations based on names.
        augmentation_list: List[Callable] = []

        for name in image_transform_names:
            # Pass dimensions if cropping / resizing, else rely on the defaults
            # as per `ImageTransformsFactory`.
            if name in {"random_resize_crop", "center_crop", "smallest_max_size"}:
                augmentation_list.append(
                    ImageTransformsFactory.create(name, _C.DATA.IMAGE_CROP_SIZE)
                )
            else:
                augmentation_list.append(ImageTransformsFactory.create(name))

        kwargs["image_transform"] = alb.Compose(augmentation_list)
        # Dataset names match with model names (and ofcourse pretext names).
        return cls.create(_C.MODEL.NAME, **kwargs)


class DownstreamDatasetFactory(Factory):
    # We use `DOWNSTREAM.LINEAR_CLF.DATA_ROOT` so these keys look like paths.
    PRODUCTS = {
        "datasets/imagenet": vdata.ImageNetDataset,
        "datasets/places205": vdata.Places205Dataset,
    }

    @classmethod
    def from_config(cls, config: Config, split: str = "train"):
        _C = config
        kwargs = {"root": _C.DOWNSTREAM.LINEAR_CLF.DATA_ROOT, "split": split}
        return cls.create(_C.DOWNSTREAM.LINEAR_CLF.DATA_ROOT, **kwargs)


class VisualStreamFactory(Factory):

    PRODUCTS = {
        "blind": vs.BlindVisualStream,
        "torchvision": vs.TorchvisionVisualStream,
        "detectron2": vs.D2BackboneVisualStream,
    }

    @classmethod
    def from_config(cls, config: Config) -> vs.VisualStream:
        _C = config
        kwargs = {"visual_feature_size": _C.MODEL.VISUAL.FEATURE_SIZE}
        if (
            "torchvision" in _C.MODEL.VISUAL.NAME
            or "detectron2" in _C.MODEL.VISUAL.NAME
        ):
            zoo_name, cnn_name = _C.MODEL.VISUAL.NAME.split("::")
            kwargs["pretrained"] = _C.MODEL.VISUAL.PRETRAINED
            kwargs["frozen"] = _C.MODEL.VISUAL.FROZEN

            return cls.create(zoo_name, cnn_name, **kwargs)
        return cls.create(_C.MODEL.VISUAL.NAME, **kwargs)


class TextualStreamFactory(Factory):

    # fmt: off
    PRODUCTS: Dict[str, Callable] = {
        "allfuse_prenorm": partial(ts.AllLayersFusionTextualStream, norm_type="post"),
        "allfuse_postnorm": partial(ts.AllLayersFusionTextualStream, norm_type="post"),
        "lastfuse_prenorm": partial(ts.LastLayerFusionTextualStream, norm_type="post"),
        "lastfuse_postnorm": partial(ts.LastLayerFusionTextualStream, norm_type="post"),
    }
    # fmt: on

    @classmethod
    def from_config(
        cls, config: Config, tokenizer: Optional[SentencePieceBPETokenizer] = None
    ) -> nn.Module:

        _C = config
        name = _C.MODEL.TEXTUAL.NAME.split("::")[0]
        tokenizer = tokenizer or TokenizerFactory.from_config(_C)

        # Transformer will be bidirectional only for word masking pretext.
        kwargs = {
            "vocab_size": tokenizer.get_vocab_size(),
            "hidden_size": _C.MODEL.TEXTUAL.HIDDEN_SIZE,
            "dropout": _C.MODEL.DROPOUT,
            "is_bidirectional": _C.MODEL.NAME == "word_masking",
            "padding_idx": tokenizer.token_to_id("[UNK]"),
            "max_caption_length": _C.DATA.MAX_CAPTION_LENGTH,
            "feedforward_size": _C.MODEL.TEXTUAL.FEEDFORWARD_SIZE,
            "attention_heads": _C.MODEL.TEXTUAL.ATTENTION_HEADS,
            "num_layers": _C.MODEL.TEXTUAL.NUM_LAYERS,
        }
        return cls.create(name, **kwargs)


class PretrainingModelFactory(Factory):

    PRODUCTS = {
        "word_masking": vmodels.WordMaskingModel,
        "captioning": partial(vmodels.CaptioningModel, is_bidirectional=False),
        "bicaptioning": partial(vmodels.CaptioningModel, is_bidirectional=True),
        "token_classification": vmodels.TokenClassificationModel,
        "instance_classification": vmodels.InstanceClassificationModel,
    }

    @classmethod
    def from_config(
        cls, config: Config, tokenizer: Optional[SentencePieceBPETokenizer] = None
    ) -> nn.Module:

        _C = config
        tokenizer = tokenizer or TokenizerFactory.from_config(_C)

        # Build visual and textual streams based on config.
        visual = VisualStreamFactory.from_config(_C)
        textual = TextualStreamFactory.from_config(_C, tokenizer)

        # Add model specific kwargs. Refer call signatures of specific models
        # for matching kwargs here.
        kwargs = {}
        if _C.MODEL.NAME == "captioning":
            kwargs.update(
                max_decoding_steps=_C.DATA.MAX_CAPTION_LENGTH,
                sos_index=tokenizer.token_to_id("[SOS]"),
                eos_index=tokenizer.token_to_id("[EOS]"),
            )

        elif _C.MODEL.NAME == "token_classification":
            kwargs.update(
                vocab_size=tokenizer.get_vocab_size(),
                ignore_indices=[
                    tokenizer.token_to_id("[UNK]"),
                    tokenizer.token_to_id("[SOS]"),
                    tokenizer.token_to_id("[EOS]"),
                    tokenizer.token_to_id("[MASK]"),
                ],
            )
        # Let the default values in `instance_classification` do the job right
        # now. Change them later.

        return cls.create(_C.MODEL.NAME, visual, textual, **kwargs)


class OptimizerFactory(Factory):

    PRODUCTS = {"sgd": optim.SGD, "adam": optim.Adam, "adamw": optim.AdamW}

    @classmethod
    def from_config(  # type: ignore
        cls, config: Config, named_parameters: Iterable[Any]
    ) -> optim.Optimizer:
        _C = config

        # Form param groups on two criterions:
        #   1. no weight decay for some parameters (usually norm and bias)
        #   2. different LR and weight decay for CNN and rest of model.
        # fmt: off
        param_groups: List[Dict[str, Any]] = []
        for name, param in named_parameters:
            lr = _C.OPTIM.CNN_LR if "cnn" in name else _C.OPTIM.LR

            is_no_decay = any(n in name for n in _C.OPTIM.NO_DECAY)
            wd = 0.0 if is_no_decay else _C.OPTIM.WEIGHT_DECAY

            param_groups.append({"params": [param], "lr": lr, "weight_decay": wd})
        # fmt: on

        if "adam" in _C.OPTIM.OPTIMIZER_NAME:
            kwargs = {"betas": tuple(_C.OPTIM.ADAM_BETAS)}
        else:
            kwargs = {"momentum": _C.OPTIM.SGD_MOMENTUM}

        optimizer = cls.create(_C.OPTIM.OPTIMIZER_NAME, param_groups, **kwargs)
        if _C.OPTIM.USE_LOOKAHEAD:
            optimizer = Lookahead(
                optimizer, k=_C.OPTIM.LOOKAHEAD_STEPS, alpha=_C.OPTIM.LOOKAHEAD_ALPHA
            )
        return optimizer


class LRSchedulerFactory(Factory):

    PRODUCTS = {
        "none": lr_scheduler.LinearWarmupNoDecayLR,
        "linear": lr_scheduler.LinearWarmupLinearDecayLR,
        "cosine": lr_scheduler.LinearWarmupCosineAnnealingLR,
    }

    @classmethod
    def from_config(  # type: ignore
        cls, config: Config, optimizer: optim.Optimizer
    ) -> optim.lr_scheduler.LambdaLR:
        _C = config
        return cls.create(
            _C.OPTIM.LR_DECAY_NAME,
            optimizer,
            total_steps=_C.OPTIM.NUM_ITERATIONS,
            warmup_steps=_C.OPTIM.WARMUP_STEPS,
        )
