# SPDX-FileCopyrightText: 2018-2022 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#

# Import protocomm protobuf modules from local bundled directory
# This replaces the original IDF_PATH-based loading
from ...protocomm import (
    constants_pb2,
    sec0_pb2,
    sec1_pb2,
    sec2_pb2,
    session_pb2,
)

# WiFi provisioning protobufs are not needed for ESP Local Control
# They were originally loaded from IDF_PATH but are not used in this integration

__all__ = ["constants_pb2", "sec0_pb2", "sec1_pb2", "sec2_pb2", "session_pb2"]
