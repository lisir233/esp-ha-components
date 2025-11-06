# SPDX-FileCopyrightText: 2018-2022 Espressif Systems (Shanghai) CO LTD
# SPDX-License-Identifier: Apache-2.0
#

"""ESP provisioning module - only security and transport submodules are used for ESP Local Control."""

# Only security and transport modules are needed for ESP Local Control
# WiFi provisioning functionality from esp_prov.py is not used

# Import submodules to make them accessible via esp_prov.transport and esp_prov.security
from . import security
from . import transport

__all__ = ["security", "transport"]
