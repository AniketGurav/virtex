from collections import defaultdict
import os
import glob
from typing import Dict, List, Tuple

import dataflow as df
import numpy as np
from PIL import Image
import torch
from torch.utils.data import IterableDataset

from viswsl.data.dataflows import TransformImageForResNetLikeModels


class VOC07ClassificationDataset(IterableDataset):

    def __init__(self, voc_root: str, split: str = "train"):
        ann_paths = glob.glob(
            os.path.join(voc_root, "ImageSets", "Main", f"*_{split}.txt")
        )
        self._image_dir = os.path.join(voc_root, "JPEGImages")

        # A list like; ["aeroplane", "bicycle", "bird", ...]
        self._class_names = [
            os.path.basename(path).split("_")[0] for path in ann_paths
        ]
        # We will construct a map for image name to a list of
        # shape: (num_classes, ) and values as one of {-1, 0, 1}.
        # 1: present, -1: not present, 0: ignore.
        image_names_to_labels: Dict[str, torch.Tensor] = defaultdict(
            lambda: -torch.ones(len(self._class_names), dtype=torch.int32)
        )
        for cls_num, ann_path in enumerate(sorted(ann_paths)):
            with open(ann_path, "r") as fopen:
                for line in fopen:
                    img_name, orig_label_str = line.strip().split()
                    orig_label = int(orig_label_str)

                    # In VOC data, -1 (not present): set to 0 as train target
                    # In VOC data, 0 (ignore): set to -1 as train target.
                    orig_label = (
                        0 if orig_label == -1 else -1 if orig_label == 0 else 1
                    )
                    image_names_to_labels[img_name][cls_num] = orig_label

        # Convert the dict to a list of tuples for easy indexing.
        # Replace image name with full image path.
        instances: List[Tuple[str, torch.Tensor]] = [
            (os.path.join(voc_root, "JPEGImages", f"{image_name}.jpg"), label)
            for image_name, label in image_names_to_labels.items()
        ]

        self._pipeline = df.DataFromList(instances, shuffle=split == "train")

        # Read image from file path.
        self._pipeline = df.MapDataComponent(self._pipeline, Image.open, index=0)
        self._pipeline = df.MapDataComponent(self._pipeline, np.array, index=0)

        # Transform image component as we are using a Resnet-like model.
        self._pipeline = TransformImageForResNetLikeModels(
            self._pipeline, index_or_key=0
        )

    @property
    def class_names(self):
        return self._class_names

    def __len__(self):
        return len(self._pipeline)

    def __iter__(self):
        self._pipeline.reset_state()

        for datapoint in self._pipeline:
            yield {"image": datapoint[0], "label": datapoint[1]}
