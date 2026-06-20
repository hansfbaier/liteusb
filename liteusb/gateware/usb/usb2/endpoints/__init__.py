#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

"""
The ``endpoints`` module contains implementations of various useful endpoint interfaces.
"""

from .isochronous import USBIsochronousInEndpoint
from .isochronous_stream_in import USBIsochronousStreamInEndpoint
from .isochronous_stream_out import USBIsochronousStreamOutEndpoint
