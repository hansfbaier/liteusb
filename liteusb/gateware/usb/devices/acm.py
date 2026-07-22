#
# This file is part of LiteUSB.
#
# Copyright (c) 2020 Great Scott Gadgets <info@greatscottgadgets.com>
# Copyright (c) 2026 Hans Baier <foss@hans-baier.de>
# SPDX-License-Identifier: BSD-3-Clause

""" Pre-made gateware that implements CDC-ACM serial for LiteUSB/LiteX. """

from migen import *
from migen.genlib.fifo import AsyncFIFO

from litex.soc.interconnect import stream

from ..usb2.device import USBDevice
from ..usb2.control import USBControlEndpoint
from ..usb2.endpoints.stream import USBStreamInEndpoint, USBStreamOutEndpoint
from ..usb2.request import USBRequestHandler, StallOnlyRequestHandler
from ..stream import StreamInterface


# USB Request Type constants
USB_REQUEST_TYPE_STANDARD = 0
USB_REQUEST_TYPE_CLASS    = 1
USB_REQUEST_TYPE_VENDOR   = 2
USB_REQUEST_TYPE_RESERVED = 3


class ACMRequestHandler(USBRequestHandler):
    """ Minimal set of request handlers to implement ACM functionality.

    Implements just enough of the requests to be usable on major operating systems.
    In testing, macOS and Linux are fine with all requests being stalled; while Windows
    seems to be happy as long as SET_LINE_CODING is implemented. We'll implement only
    that, and stall every other handler.
    """

    SET_LINE_CODING = 0x20

    def __init__(self):
        super().__init__()

    def do_finalize(self):
        interface = self.interface
        setup = interface.setup

        #
        # Class request handlers.
        #
        self.comb += If(setup.type == USB_REQUEST_TYPE_CLASS,
            # Handle SET_LINE_CODING request
            If(setup.request == self.SET_LINE_CODING,
                # Drive interface outputs for this request
                interface.claim.eq(1),

                # Always ACK the data out...
                If(interface.rx_ready_for_response,
                    interface.handshakes_out.ack.eq(1)
                ),

                # ... and accept whatever the request was.
                If(interface.status_requested,
                    *self.send_zlp()
                )
            )
        )


class USBACMSerialDevice(Module):
    """ Device that acts as a CDC-ACM 'serial converter' for LiteX.

    Provides a UART-like interface using LiteX stream Endpoints, making it
    compatible with the LiteX ecosystem.

    Attributes
    ----------
    connect: Signal(), input
        When asserted, the USB-to-serial device will be presented to the host
        and allowed to communicate.
    full_speed_only: Signal(), input
        When asserted, the device operates at Full Speed only (no HS chirp).
    
    # UART-like interface (compatible with litex.soc.cores.uart)
    sink: stream.Endpoint([("data", 8)]), input
        Stream endpoint for data to be transmitted to the host (TX).
    source: stream.Endpoint([("data", 8)]), output
        Stream endpoint for data received from the host (RX).

    Parameters
    ----------
    bus: Record()
        The raw input record that provides our USB connection. Should be a connection 
        to a USB PHY, SerDes, or raw USB lines.
    idVendor: int, <65536
        The Vendor ID that should be presented for the relevant USB device.
    idProduct: int, <65536
        The Product ID that should be presented for the relevant USB device.
    manufacturer_string: str, optional
        A string describing this device's manufacturer.
    product_string: str, optional
        A string describing this device.
    serial_number: str, optional
        A string describing this device's serial number.
    max_packet_size: int in {64, 256, 512}, optional
        The maximum packet size for communications. Default is 64.
    """

    _STATUS_ENDPOINT_NUMBER = 3
    _DATA_ENDPOINT_NUMBER   = 4

    def __init__(self, bus, idVendor, idProduct,
            manufacturer_string="LiteUSB",
            product_string="USB-to-serial",
            serial_number="", max_packet_size=64, handle_clocking=True):

        self._bus                 = bus
        self._idVendor            = idVendor
        self._idProduct           = idProduct
        self._manufacturer_string = manufacturer_string
        self._product_string      = product_string
        self._serial_number       = serial_number
        self._max_packet_size     = max_packet_size
        self._handle_clocking     = handle_clocking

        #
        # I/O port
        #
        self.connect = Signal()
        self.full_speed_only = Signal()
        self.sink   = stream.Endpoint([("data", 8)])    # Data to host (TX)
        self.source = stream.Endpoint([("data", 8)])    # Data from host (RX)

        # Create our core USB device here (not in do_finalize) so integrators
        # can access its signals (LEDs, probes) before finalization.
        # All endpoint configuration also happens here: since ``usb`` is a
        # submodule, its do_finalize runs before ours, so endpoints must be
        # registered before finalization starts.
        self.submodules.usb = self.usb = USBDevice(bus=self._bus, handle_clocking=self._handle_clocking)
        self._configure()

    def create_descriptors(self):
        """ Creates the descriptors that describe our serial topology. """
        try:
            from usb_protocol.emitters import DeviceDescriptorCollection
            from usb_protocol.emitters.descriptors import cdc
        except ImportError:
            raise ImportError("usb_protocol library is required for USB descriptor generation")

        descriptors = DeviceDescriptorCollection()

        # Create a device descriptor with our user parameters...
        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = self._idVendor
            d.idProduct          = self._idProduct

            d.iManufacturer      = self._manufacturer_string
            d.iProduct           = self._product_string
            d.iSerialNumber      = self._serial_number

            d.bNumConfigurations = 1

        # ... and then describe our CDC-ACM setup.
        with descriptors.ConfigurationDescriptor() as c:
            # First, add a descriptor to show that both interfaces in this
            # configuration are associated with the same function.
            # (this seems to be required on Windows)
            with c.InterfaceAssociationDescriptor() as ia:
                ia.bFirstInterface = 0
                ia.bInterfaceCount = 2
                ia.bFunctionClass    = 0x02 # CDC
                ia.bFunctionSubClass = 0x02 # ACM
                ia.bFunctionProtocol = 0x01 # AT commands / UART

            # Then, we'll describe the Communication Interface, which contains most
            # of our description; but also an endpoint that does effectively nothing in
            # our case, since we don't have interrupts we want to send up to the host.
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber   = 0

                i.bInterfaceClass    = 0x02 # CDC
                i.bInterfaceSubclass = 0x02 # ACM
                i.bInterfaceProtocol = 0x01 # AT commands / UART

                # Provide the default CDC version.
                i.add_subordinate_descriptor(cdc.HeaderDescriptorEmitter())

                # ... specify our interface associations ...
                union = cdc.UnionFunctionalDescriptorEmitter()
                union.bControlInterface      = 0
                union.bSubordinateInterface0 = 1
                i.add_subordinate_descriptor(union)

                # ... and specify the interface that'll carry our data...
                call_management = cdc.CallManagementFunctionalDescriptorEmitter()
                call_management.bDataInterface = 1
                i.add_subordinate_descriptor(call_management)

                # CDC communications endpoint
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | self._STATUS_ENDPOINT_NUMBER
                    e.bmAttributes     = 0x03
                    e.wMaxPacketSize   = self._max_packet_size
                    e.bInterval        = 11

            # Finally, we'll describe the communications interface, which just has the
            # endpoints for our data in and out.
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber   = 1
                i.bInterfaceClass    = 0x0a # CDC data
                i.bInterfaceSubclass = 0x00
                i.bInterfaceProtocol = 0x00

                # Data IN to host (tx, from our side)
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | self._DATA_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = self._max_packet_size

                # Data OUT from host (rx, from our side)
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = self._DATA_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = self._max_packet_size

        return descriptors

    def _configure(self):
        # Add a standard control endpoint to our core USB device.
        usb = self.usb
        control_ep = usb.add_standard_control_endpoint(self.create_descriptors())

        # Attach our class request handlers.
        acm_handler = ACMRequestHandler()
        self.submodules.acm_handler = acm_handler
        control_ep.add_request_handler(acm_handler)

        # Attach class-request handlers that stall any vendor or reserved requests,
        # as we don't have or need any.
        def stall_condition(setup):
            return (setup.type == USB_REQUEST_TYPE_VENDOR) | (setup.type == USB_REQUEST_TYPE_RESERVED)
        
        stall_handler = StallOnlyRequestHandler(stall_condition)
        self.submodules.stall_handler = stall_handler
        control_ep.add_request_handler(stall_handler)

        # Create our status/communications endpoint; but don't ever drive its stream.
        # This should be optimized down to an endpoint that always NAKs.
        serial_status_ep = USBStreamInEndpoint(
            endpoint_number=self._STATUS_ENDPOINT_NUMBER,
            max_packet_size=self._max_packet_size
        )
        self.submodules.serial_status_ep = serial_status_ep
        usb.add_endpoint(serial_status_ep)

        # Create an endpoint for serial rx (data from host)...
        serial_rx_endpoint = USBStreamOutEndpoint(
            endpoint_number=self._DATA_ENDPOINT_NUMBER,
            max_packet_size=self._max_packet_size,
        )
        self.submodules.serial_rx_endpoint = serial_rx_endpoint
        usb.add_endpoint(serial_rx_endpoint)

        # ... and one for serial tx (data to host).
        serial_tx_endpoint = USBStreamInEndpoint(
            endpoint_number=self._DATA_ENDPOINT_NUMBER,
            max_packet_size=self._max_packet_size
        )
        self.submodules.serial_tx_endpoint = serial_tx_endpoint
        usb.add_endpoint(serial_tx_endpoint)

        # Create bridge FIFOs between LiteX stream interface and USB stream interface.
        # These cross clock domains: the USB endpoint side runs on "usb", the
        # application (sink/source) side runs on "sync", so asynchronous FIFOs
        # with proper CDC are required (a plain SyncFIFO in the wrong domain
        # silently eats data — e.g. the ACM loopback echoing only zeros).
        # TX FIFO: LiteX sink -> USB TX endpoint
        self.submodules.tx_fifo = tx_fifo = ClockDomainsRenamer(
            {"write": "sys", "read": "usb"})(
            AsyncFIFO(width=8, depth=self._max_packet_size * 2))

        # RX FIFO: USB RX endpoint -> LiteX source
        self.submodules.rx_fifo = rx_fifo = ClockDomainsRenamer(
            {"write": "usb", "read": "sys"})(
            AsyncFIFO(width=8, depth=self._max_packet_size * 2))

        # Create LiteUSB StreamInterface adapters
        tx_stream = StreamInterface()
        rx_stream = StreamInterface()

        # Connect up our I/O.
        # USB TX: Data from LiteX sink -> USB host
        self.comb += [
            # LiteX sink -> TX FIFO
            tx_fifo.we.eq(self.sink.valid),
            tx_fifo.din.eq(self.sink.data),
            self.sink.ready.eq(tx_fifo.writable),

            # TX FIFO -> USB TX stream
            tx_stream.valid.eq(tx_fifo.readable),
            tx_stream.payload.eq(tx_fifo.dout),
            tx_fifo.re.eq(tx_stream.ready),

            # Connect to USB endpoint.  Assert flush so partial
            # packets are sent immediately — without this, the IN
            # transfer manager waits for a full max_packet_size (64)
            # before transmitting, causing console output to appear
            # buffered / swallowed.
            serial_tx_endpoint.stream.stream_eq(tx_stream),
            serial_tx_endpoint.flush.eq(1),
        ]

        # USB RX: Data from USB host -> LiteX source
        self.comb += [
            # USB RX stream -> RX FIFO
            rx_stream.stream_eq(serial_rx_endpoint.stream),
            rx_fifo.we.eq(rx_stream.valid),
            rx_fifo.din.eq(rx_stream.payload),
            rx_stream.ready.eq(rx_fifo.writable),

            # RX FIFO -> LiteX source
            self.source.valid.eq(rx_fifo.readable),
            self.source.data.eq(rx_fifo.dout),
            rx_fifo.re.eq(self.source.ready),
            self.source.first.eq(0),
            self.source.last.eq(0),

            # USB connect and speed
            usb.connect.eq(self.connect),
            usb.full_speed_only.eq(self.full_speed_only),
        ]

    def add_core_interfaces(self, soc):
        """ Helper method to add this device to a LiteX SoC.
        
        This adds the USB device as a submodule and exposes the UART interface
        for use with other LiteX components.
        """
        soc.submodules.usb_serial = self
