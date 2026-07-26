#!/usr/bin/env python3
"""Generate a deterministic reference ONNX graph for native runtime proof."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

SCHEMA = "echoes.reference-onnx-model.v1"


def build_model() -> onnx.ModelProto:
    input_info = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 4])
    output_info = helper.make_tensor_value_info("output", TensorProto.FLOAT, [1, 4])
    scale = numpy_helper.from_array(np.asarray([2.0], dtype=np.float32), name="scale")
    bias = numpy_helper.from_array(np.asarray([1.0], dtype=np.float32), name="bias")
    nodes = [
        helper.make_node("Mul", ["input", "scale"], ["scaled"], name="ScaleByTwo"),
        helper.make_node("Add", ["scaled", "bias"], ["output"], name="AddOne"),
    ]
    graph = helper.make_graph(
        nodes,
        "EchoesReferenceTransform",
        [input_info],
        [output_info],
        [scale, bias],
    )
    model = helper.make_model(
        graph,
        producer_name="EchoesEngine",
        producer_version="1",
        opset_imports=[helper.make_opsetid("", 13)],
    )
    model.ir_version = 8
    model.doc_string = "Deterministic reference graph: output = input * 2 + 1"
    onnx.checker.check_model(model)
    return model


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--metadata", type=Path, required=True)
    args = parser.parse_args()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.metadata.parent.mkdir(parents=True, exist_ok=True)
    model = build_model()
    args.output.write_bytes(model.SerializeToString())
    payload = args.output.read_bytes()
    metadata = {
        "schema": SCHEMA,
        "path": args.output.as_posix(),
        "sizeBytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "input": {"name": "input", "shape": [1, 4], "type": "float32"},
        "output": {"name": "output", "shape": [1, 4], "type": "float32"},
        "operation": "output = input * 2 + 1",
        "productionModel": False,
        "voiceConversionModel": False,
        "videoModel": False,
    }
    args.metadata.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metadata, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
