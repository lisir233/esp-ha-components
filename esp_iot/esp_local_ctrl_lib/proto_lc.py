# SPDX-FileCopyrightText: 2018-2023 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#

from importlib.abc import Loader
import importlib.util
from pathlib import Path
import sys
from typing import Any


def _load_source(name: str, path: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    if not spec:
        return None

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert isinstance(spec.loader, Loader)
    spec.loader.exec_module(module)
    return module


# Use relative path to bundled library components
_current_file = Path(__file__)
_lib_root = _current_file.parent

constants_pb2 = _load_source(
    "constants_pb2",
    str(_lib_root / "protocomm" / "constants_pb2.py"),
)
local_ctrl_pb2 = _load_source(
    "esp_local_ctrl_pb2",
    str(_lib_root / "esp_local_ctrl_pb2.py"),
)


def to_bytes(s: str) -> bytes:
    # Handle both str and bytes input
    if isinstance(s, bytes):
        return s
    return bytes(s, encoding="latin-1")


def get_prop_count_request(security_ctx):
    req = local_ctrl_pb2.LocalCtrlMessage()
    req.msg = local_ctrl_pb2.TypeCmdGetPropertyCount
    payload = local_ctrl_pb2.CmdGetPropertyCount()
    req.cmd_get_prop_count.MergeFrom(payload)
    enc_cmd = security_ctx.encrypt_data(req.SerializeToString())
    return enc_cmd.decode("latin-1")


def get_prop_count_response(security_ctx, response_data):
    decrypt = security_ctx.decrypt_data(to_bytes(response_data))
    resp = local_ctrl_pb2.LocalCtrlMessage()
    resp.ParseFromString(decrypt)
    if resp.resp_get_prop_count.status == 0:
        return resp.resp_get_prop_count.count
    return 0


def get_prop_vals_request(security_ctx, indices):
    req = local_ctrl_pb2.LocalCtrlMessage()
    req.msg = local_ctrl_pb2.TypeCmdGetPropertyValues
    payload = local_ctrl_pb2.CmdGetPropertyValues()
    payload.indices.extend(indices)
    req.cmd_get_prop_vals.MergeFrom(payload)
    enc_cmd = security_ctx.encrypt_data(req.SerializeToString())
    return enc_cmd.decode("latin-1")


def get_prop_vals_response(security_ctx, response_data):
    decrypt = security_ctx.decrypt_data(to_bytes(response_data))
    resp = local_ctrl_pb2.LocalCtrlMessage()
    resp.ParseFromString(decrypt)
    results = []
    if resp.resp_get_prop_vals.status == 0:
        for prop in resp.resp_get_prop_vals.props:
            results += [{"index": prop.index, "value": prop.value}]
    return results


def set_prop_vals_request(security_ctx, indices, values):
    req = local_ctrl_pb2.LocalCtrlMessage()
    req.msg = local_ctrl_pb2.TypeCmdSetPropertyValues
    payload = local_ctrl_pb2.CmdSetPropertyValues()
    for i, v in zip(indices, values, strict=False):
        prop = payload.props.add()
        prop.index = i
        prop.value = v
    req.cmd_set_prop_vals.MergeFrom(payload)
    enc_cmd = security_ctx.encrypt_data(req.SerializeToString())
    return enc_cmd.decode("latin-1")


def set_prop_vals_response(security_ctx, response_data):
    decrypt = security_ctx.decrypt_data(to_bytes(response_data))
    resp = local_ctrl_pb2.LocalCtrlMessage()
    resp.ParseFromString(decrypt)
    return resp.resp_set_prop_vals.status == 0


def parse_payload(msg_type_str, security_ctx, payload_data):
    """Parse encrypted LocalCtrlMessage from ESP32

    Args:
        msg_type_str: Message type ('active_report', 'query_response', or header-derived type)
        security_ctx: Security context for decryption
        payload_data: Raw encrypted bytes to decrypt and parse

    Returns:
        dict with 'status', 'properties', and optional 'count' or 'error'
    """
    print(
        f"[DEBUG] parse_payload: Called with msg_type_str={msg_type_str}, payload_len={len(payload_data)}"
    )

    try:
        # Decrypt the payload
        print(
            f"[DEBUG] parse_payload: Decrypting payload ({len(payload_data)} bytes)..."
        )
        decrypt_data = security_ctx.decrypt_data(to_bytes(payload_data))
        print(
            f"[DEBUG] parse_payload: Decryption successful, decrypted {len(decrypt_data)} bytes"
        )

        # Parse protobuf
        print("[DEBUG] parse_payload: Parsing LocalCtrlMessage protobuf...")
        resp = local_ctrl_pb2.LocalCtrlMessage()
        resp.ParseFromString(decrypt_data)
        print("[DEBUG] parse_payload: Protobuf parsed successfully")

        # Check for resp_get_prop_count (for count-only responses)
        if resp.HasField("resp_get_prop_count"):
            print("[DEBUG] parse_payload: Found resp_get_prop_count field")
            # Check if also has resp_get_prop_vals
            if resp.resp_get_prop_vals and len(resp.resp_get_prop_vals.props) == 0:
                # Both fields present but properties is empty - return count only
                print(
                    f"[DEBUG] parse_payload: Both fields present, extracting count={resp.resp_get_prop_count.count}"
                )
                return {
                    "status": 0,
                    "count": resp.resp_get_prop_count.count,
                    "properties": [],
                }

        results = []

        # Handle different message types
        if msg_type_str == "active_report":
            # ✅ Active reports use report_prop_vals field (not resp_get_prop_vals!)
            print("[DEBUG] parse_payload: Message type is 'active_report'")
            if hasattr(resp, "report_prop_vals") and resp.report_prop_vals:
                print(
                    f"[DEBUG] parse_payload: Processing active report with report_prop_vals, props count={len(resp.report_prop_vals.props)}"
                )
                for prop in resp.report_prop_vals.props:
                    results.append({"index": prop.index, "value": prop.value})
                print(
                    f"[DEBUG] parse_payload: Extracted {len(results)} properties from active_report"
                )
            else:
                print(
                    "[DEBUG] parse_payload: No report_prop_vals found in active_report message"
                )

        elif msg_type_str == "query_response":
            # Query responses contain property values in resp_get_prop_vals
            print("[DEBUG] parse_payload: Message type is 'query_response'")
            if hasattr(resp, "resp_get_prop_vals") and resp.resp_get_prop_vals:
                print(
                    f"[DEBUG] parse_payload: Found resp_get_prop_vals with status={resp.resp_get_prop_vals.status}, props count={len(resp.resp_get_prop_vals.props)}"
                )
                if resp.resp_get_prop_vals.status == 0:
                    for prop in resp.resp_get_prop_vals.props:
                        results.append(
                            {
                                "name": prop.name,
                                "type": prop.type,
                                "flags": prop.flags,
                                "value": prop.value,
                            }
                        )
                    print(
                        f"[DEBUG] parse_payload: Extracted {len(results)} properties from query_response"
                    )
            else:
                print(
                    "[DEBUG] parse_payload: No resp_get_prop_vals found in query_response"
                )

        else:
            print(f"[DEBUG] parse_payload: Unknown message type: {msg_type_str}")
            print(
                f"[DEBUG] parse_payload: resp.msg enum={resp.msg if hasattr(resp, 'msg') else 'N/A'}"
            )

            # Try to extract from report_prop_vals as fallback
            if hasattr(resp, "report_prop_vals") and resp.report_prop_vals:
                print(
                    f"[DEBUG] parse_payload: Extracting report_prop_vals from unknown message, props count={len(resp.report_prop_vals.props)}"
                )
                for prop in resp.report_prop_vals.props:
                    results.append({"index": prop.index, "value": prop.value})
                print(
                    f"[DEBUG] parse_payload: Extracted {len(results)} properties from report_prop_vals fallback"
                )
            else:
                print(
                    "[DEBUG] parse_payload: No report_prop_vals found in unknown message"
                )

        print(
            f"[DEBUG] parse_payload: Returning with status=0, {len(results)} properties"
        )
        return {"status": 0, "properties": results}

    except Exception as e:
        print(
            f"[ERROR] parse_payload: Exception during parsing - {type(e).__name__}: {e}"
        )
        print(
            f"[ERROR] parse_payload: msg_type_str={msg_type_str}, payload_len={len(payload_data)}"
        )
        print(
            f"[ERROR] parse_payload: First 100 bytes of payload (hex): {payload_data[:100].hex() if len(payload_data) > 0 else 'EMPTY'}"
        )
        import traceback

        traceback.print_exc()
        return {"status": -1, "properties": [], "error": str(e)}
