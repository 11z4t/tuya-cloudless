"""UDP broadcast discovery for Tuya devices on local network."""

import asyncio
import json
import logging
from typing import Any

from .crypto import decrypt_payload
from .exceptions import TuyaDiscoveryError

_LOGGER = logging.getLogger(__name__)

# Discovery constants
DISCOVERY_PORT = 6666
DISCOVERY_TIMEOUT = 3.0
BROADCAST_MESSAGE = b"yGAdlopoPVldABfn"


class TuyaDevice:
    """Represents a discovered Tuya device."""

    def __init__(self, data: dict[str, Any], ip: str) -> None:
        """Initialize discovered device.

        Args:
            data: Discovery response data
            ip: Device IP address
        """
        self.ip = ip
        self.device_id = data.get("gwId", data.get("devId", ""))
        self.product_key = data.get("productKey", "")
        self.protocol_version = data.get("version", "3.3")
        self.encrypted = data.get("encrypted", False)
        self.raw_data = data

    def __repr__(self) -> str:
        """String representation."""
        return (
            f"TuyaDevice(ip={self.ip}, device_id={self.device_id}, "
            f"version={self.protocol_version})"
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "ip": self.ip,
            "device_id": self.device_id,
            "product_key": self.product_key,
            "protocol_version": self.protocol_version,
            "encrypted": self.encrypted,
            "raw_data": self.raw_data,
        }


class TuyaDiscovery:
    """UDP broadcast discovery for Tuya devices."""

    def __init__(
        self,
        broadcast_address: str = "255.255.255.255",
        port: int = DISCOVERY_PORT,
        timeout: float = DISCOVERY_TIMEOUT,
    ) -> None:
        """Initialize discovery.

        Args:
            broadcast_address: Broadcast address for discovery
            port: UDP port for discovery
            timeout: Discovery timeout in seconds
        """
        self.broadcast_address = broadcast_address
        self.port = port
        self.timeout = timeout
        self._discovered_devices: dict[str, TuyaDevice] = {}

    async def discover(self, local_key: str | None = None) -> list[TuyaDevice]:
        """Discover Tuya devices on the network.

        Args:
            local_key: Optional local key for decrypting encrypted responses

        Returns:
            List of discovered TuyaDevice objects

        Raises:
            TuyaDiscoveryError: If discovery fails
        """
        _LOGGER.info("Starting Tuya device discovery on %s:%d", self.broadcast_address, self.port)
        self._discovered_devices = {}

        try:
            # Create UDP socket
            loop = asyncio.get_event_loop()
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: TuyaDiscoveryProtocol(self, local_key),
                local_addr=("0.0.0.0", 0),
                allow_broadcast=True,
            )

            try:
                # Send broadcast message
                transport.sendto(BROADCAST_MESSAGE, (self.broadcast_address, self.port))
                _LOGGER.debug("Broadcast message sent to %s:%d", self.broadcast_address, self.port)

                # Wait for responses
                await asyncio.sleep(self.timeout)

            finally:
                transport.close()

            devices = list(self._discovered_devices.values())
            _LOGGER.info("Discovery complete: found %d device(s)", len(devices))
            return devices

        except (OSError, ValueError) as e:
            raise TuyaDiscoveryError(f"Discovery failed: {e}") from e

    def _add_device(self, device: TuyaDevice) -> None:
        """Add discovered device (internal).

        Args:
            device: Discovered device
        """
        device_key = f"{device.ip}:{device.device_id}"

        if device_key not in self._discovered_devices:
            _LOGGER.info("Discovered device: %s", device)
            self._discovered_devices[device_key] = device
        else:
            _LOGGER.debug("Device already discovered: %s", device_key)


class TuyaDiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol handler for UDP discovery responses."""

    def __init__(self, discovery: TuyaDiscovery, local_key: str | None) -> None:
        """Initialize protocol handler.

        Args:
            discovery: Parent TuyaDiscovery instance
            local_key: Optional local key for decryption
        """
        self.discovery = discovery
        self.local_key = local_key
        self.transport: asyncio.BaseTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        """Called when connection is established.

        Args:
            transport: Transport for sending/receiving
        """
        self.transport = transport
        _LOGGER.debug("Discovery protocol connection established")

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Called when datagram is received.

        Args:
            data: Received data
            addr: Sender address (ip, port)
        """
        ip, port = addr
        _LOGGER.debug("Received discovery response from %s:%d (%d bytes)", ip, port, len(data))

        try:
            # Try to parse as JSON
            try:
                response = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # If not JSON, might be encrypted
                if self.local_key:
                    _LOGGER.debug("Response not JSON, attempting decryption")
                    try:
                        decrypted = decrypt_payload(data, self.local_key, "3.3")
                        response = json.loads(decrypted.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
                        _LOGGER.warning("Failed to decrypt response from %s: %s", ip, e)
                        return
                else:
                    _LOGGER.warning("Received encrypted response but no local_key provided")
                    return

            # Create device object
            device = TuyaDevice(response, ip)
            self.discovery._add_device(device)

        except (KeyError, TypeError, AttributeError) as e:
            _LOGGER.warning("Failed to process discovery response from %s: %s", ip, e)

    def error_received(self, exc: Exception) -> None:
        """Called when error is received.

        Args:
            exc: Exception that occurred
        """
        _LOGGER.error("Discovery protocol error: %s", exc)


async def discover_devices(
    broadcast_address: str = "255.255.255.255",
    timeout: float = DISCOVERY_TIMEOUT,
    local_key: str | None = None,
) -> list[TuyaDevice]:
    """Convenience function to discover Tuya devices.

    Args:
        broadcast_address: Broadcast address for discovery
        timeout: Discovery timeout in seconds
        local_key: Optional local key for decrypting responses

    Returns:
        List of discovered TuyaDevice objects
    """
    discovery = TuyaDiscovery(broadcast_address=broadcast_address, timeout=timeout)
    return await discovery.discover(local_key)
