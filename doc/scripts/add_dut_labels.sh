#!/bin/bash
# Add DUT annotations to test section headings
cd /devel/riscv/litex-root/liteusb
sed -i \
  -e 's|<h4>Token Detection Tests</h4>|<h4>Token Detection Tests — DUT: <code>USBTokenDetector</code></h4>|' \
  -e 's|<h4>Handshake Detection Tests</h4>|<h4>Handshake Detection Tests — DUT: <code>USBHandshakeDetector</code></h4>|' \
  -e 's|<h4>Data Packet Receiver Tests</h4>|<h4>Data Packet Receiver Tests — DUT: <code>USBDataPacketReceiver</code></h4>|' \
  -e 's|<h4>Data Packet Deserializer Tests</h4>|<h4>Data Packet Deserializer Tests — DUT: <code>USBDataPacketDeserializer</code></h4>|' \
  -e 's|<h4>Data Packet Generator Tests</h4>|<h4>Data Packet Generator Tests — DUT: <code>USBDataPacketGenerator</code></h4>|' \
  -e 's|<h4>Handshake Generator Tests</h4>|<h4>Handshake Generator Tests — DUT: <code>USBHandshakeGenerator</code></h4>|' \
  -e 's|<h4>Interpacket Timer Tests</h4>|<h4>Interpacket Timer Tests — DUT: <code>USBInterpacketTimer</code></h4>|' \
  -e 's|<h3>10.2 Endpoint Tests</h3>|<h3>10.2 Endpoint Tests — DUT: <code>USBIsochronousStreamInEndpoint</code>, <code>USBIsochronousStreamOutEndpoint</code></h3>|' \
  -e 's|<h3>10.3 Descriptor Tests</h3>|<h3>10.3 Descriptor Tests — DUT: <code>GetDescriptorHandlerBlock</code></h3>|' \
  -e 's|<h3>10.4 Transfer Tests</h3>|<h3>10.4 Transfer Tests — DUT: <code>USBInTransferManager</code></h3>|' \
  -e 's|<h3>10.5 Request Tests</h3>|<h3>10.5 Request Tests — DUT: <code>USBSetupDecoder</code></h3>|' \
  -e 's|<h3>10.6 Reset Tests</h3>|<h3>10.6 Reset Tests — DUT: <code>USBResetSequencer</code></h3>|' \
  -e 's|<h3>10.7 ULPI Tests</h3>|<h3>10.7 ULPI Tests — DUT: <code>ULPIRegisterWindow</code>, <code>ULPIRxEventDecoder</code>, <code>ULPIControlTranslator</code>, <code>ULPITransmitTranslator</code></h3>|' \
  -e 's|<h3>10.8 Stream Tests</h3>|<h3>10.8 Stream Tests — DUT: <code>USBOutStreamBoundaryDetector</code></h3>|' \
  -e 's|<h3>10.9 Device-Level Tests</h3>|<h3>10.9 Device-Level Tests — DUT: <code>USBDevice</code> (full)</h3>|' \
  -e 's|<h3>10.10 Integration Tests</h3>|<h3>10.10 Integration Tests — DUT: <code>USBDevice</code> (full) with <code>USBStreamOutEndpoint</code></h3>|' \
  doc/architecture.html
echo "DUT annotations added"
