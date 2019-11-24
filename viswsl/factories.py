from typing import Any, Dict, Iterable, List, Type
from torch import nn, optim

from viswsl.config import Config
from viswsl.modules import visual_stream as vstream


class Factory(object):

    PRODUCTS: Dict[str, Type[Any]] = {}

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
    def from_config(cls, config: Config, *args, **kwargs) -> Any:
        raise NotImplementedError


class VisualStreamFactory(Factory):

    PRODUCTS: Dict[str, Type[nn.Module]] = {
        "blind": vstream.BlindVisualStream,
        "torchvision": vstream.TorchvisionVisualStream,
    }

    @classmethod
    def from_config(cls, config: Config) -> Type[nn.Module]:

        _C = config
        if "torchvision" in _C.MODEL.VISUAL.NAME:
            cnn_name = _C.MODEL.VISUAL.NAME.split("::")[-1]
            kwargs = {
                "pretrained": _C.MODEL.VISUAL.PRETRAINED,
                "num_groups": _C.MODEL.VISUAL.NUM_GROUPS,
                "norm_layer": nn.BatchNorm2d
                if _C.MODEL.VISUAL.NORM_LAYER == "batchnorm"
                else nn.GroupNorm,
            }
            return cls.create("torchvision", cnn_name, **kwargs)

        return cls.create(_C.MODEL.VISUAL.NAME)


class OptimizerFactory(Factory):

    PRODUCTS: Dict[str, Type[optim.Optimizer]] = {
        "sgd": optim.SGD,
        "adam": optim.Adam,
        "adamw": optim.AdamW,
    }

    @classmethod
    def from_config(
        cls, config: Config, params: Iterable[Any]
    ) -> Type[optim.Optimizer]:
        _C = config

        # Form kwargs according to the optimizer name, different optimizers
        # may require different hyperparams in their constructor, for example:
        # `SGD` accepts "momentum" while `Adam` doesn't.
        kwargs = {"lr": _C.OPTIM.LR, "weight_decay": _C.OPTIM.WEIGHT_DECAY}
        if _C.OPTIM.OPTIMIZER_NAME == "sgd":
            kwargs["momentum"] = _C.OPTIM.SGD_MOMENTUM
            kwargs["nesterov"] = _C.OPTIM.SGD_NESTEROV

        return cls.create(_C.OPTIM.OPTIMIZER_NAME, params, **kwargs)
