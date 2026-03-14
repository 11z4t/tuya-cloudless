"""UDP broadcast discovery for Tuya devices on local network."""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from .crypto import decrypt_payload
from .exceptions import TuyaDiscoveryError

_LOGGER = logging.getLogger(__name__)

# Discovery constants
DISCOVERY_PORT_UNENCRYPTED = 6666  # v3.1-v3.3 unencrypted responses
DISCOVERY_PORT_ENCRYPTED = 6667  # v3.4-v3.5 encrypted responses
DISCOVERY_TIMEOUT = 3.0
# Tuya UDP discovery magic bytes (constant across all firmware versions)
BROADCAST_MESSAGE = b"yGAdlopoPVldABfn"
# UDP_KEY for decrypting port 6667 responses (same as broadcast message)
UDP_KEY = "yGAdlopoPVldABfn"


@dataclass(frozen=True)
class TuyaDevice:
    """Represents a discovered Tuya device."""

    ip: str
    device_id: str
    product_key: str
    protocol_version: str
    encrypted: bool
    raw_data: dict[str, Any]

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
        timeout: float = DISCOVERY_TIMEOUT,
        retries: int = 2,
    ) -> None:
        """Initialize discovery.

        Args:
            broadcast_address: Broadcast address for discovery
            timeout: Discovery timeout in seconds
            retries: Number of broadcast retries (default 2)
        """
        self.broadcast_address = broadcast_address
        self.timeout = timeout
        self.retries = retries
        self._discovered_devices: dict[str, TuyaDevice] = {}
        self._stop_event: asyncio.Event | None = None

    async def discover(self, local_key: str | None = None) -> list[TuyaDevice]:
        """Discover Tuya devices on the network.

        Args:
            local_key: Optional local key for decrypting encrypted responses

        Returns:
            List of discovered TuyaDevice objects

        Raises:
            TuyaDiscoveryError: If discovery fails
        """
        _LOGGER.info(
            "Starting Tuya device discovery on %s (ports %d, %d)",
            self.broadcast_address,
            DISCOVERY_PORT_UNENCRYPTED,
            DISCOVERY_PORT_ENCRYPTED,
        )
        self._discovered_devices = {}
        self._stop_event = asyncio.Event()

        try:
            # Create UDP sockets for both ports
            loop = asyncio.get_running_loop()

            # Port 6666 - unencrypted responses (v3.1-v3.3)
            transport_6666, protocol_6666 = await loop.create_datagram_endpoint(
                lambda: TuyaDiscoveryProtocol(self, local_key, DISCOVERY_PORT_UNENCRYPTED),
                local_addr=("0.0.0.0", 0),
                allow_broadcast=True,
            )

            # Port 6667 - encrypted responses (v3.4-v3.5)
            transport_6667, protocol_6667 = await loop.create_datagram_endpoint(
                lambda: TuyaDiscoveryProtocol(self, local_key, DISCOVERY_PORT_ENCRYPTED),
                local_addr=("0.0.0.0", 0),
                allow_broadcast=True,
            )

            try:
                # Send multiple broadcasts with delay (UDP reliability)
                for attempt in range(self.retries + 1):
                    _LOGGER.debug(
                        "Sending broadcast %d/%d to %s",
                        attempt + 1,
                        self.retries + 1,
                        self.broadcast_address,
                    )
                    transport_6666.sendto(
                        BROADCAST_MESSAGE,
                        (self.broadcast_address, DISCOVERY_PORT_UNENCRYPTED),
                    )
                    transport_6667.sendto(
                        BROADCAST_MESSAGE,
                        (self.broadcast_address, DISCOVERY_PORT_ENCRYPTED),
                    )

                    if attempt < self.retries:
                        await asyncio.sleep(0.5)

                # Wait for responses with cancellation support
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(),
                        timeout=self.timeout,
                    )
                except asyncio.TimeoutError:
                    pass  # Normal timeout

            finally:
                transport_6666.close()
                transport_6667.close()

            devices = list(self._discovered_devices.values())
            _LOGGER.info("Discovery complete: found %d device(s)", len(devices))
            return devices

        except asyncio.CancelledError:
            _LOGGER.info("Discovery cancelled")
            raise
        except (OSError, ValueError) as e:
            raise TuyaDiscoveryError(f"Discovery failed: {e}") from e

    def _add_device(self, data: dict[str, Any], ip: str) -> None:
        """Add discovered device (internal).

        Args:
            data: Discovery response data
            ip: Device IP address
        """
        device_id = data.get("gwId", data.get("devId", ""))
        device_key = f"{ip}:{device_id}"

        if device_key not in self._discovered_devices:
            device = TuyaDevice(
                ip=ip,
                device_id=device_id,
                product_key=data.get("productKey", ""),
                protocol_version=data.get("version", "3.3"),
                encrypted=data.get("encrypted", False),
                raw_data=data,
            )
            _LOGGER.info("Discovered device: %s", device)
            self._discovered_devices[device_key] = device
        else:
            _LOGGER.debug("Device already discovered: %s", device_key)


class TuyaDiscoveryProtocol(asyncio.DatagramProtocol):
    """Protocol handler for UDP discovery responses."""

    def __init__(
        self, discovery: TuyaDiscovery, local_key: str | None, listen_port: int
    ) -> None:
        """Initialize protocol handler.

        Args:
            discovery: Parent TuyaDiscovery instance
            local_key: Optional local key for decryption
            listen_port: Port we're listening on (6666 or 6667)
        """
        self.discovery = discovery
        self.local_key = local_key
        self.listen_port = listen_port
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
        _LOGGER.debug(
            "Received discovery response from %s:%d on port %d (%d bytes)",
            ip,
            port,
            self.listen_port,
            len(data),
        )

        try:
            response: dict[str, Any]

            # Port 6667 responses are always encrypted with UDP_KEY
            if self.listen_port == DISCOVERY_PORT_ENCRYPTED:
                _LOGGER.debug("Port 6667 response - decrypting with UDP_KEY")
                try:
                    # Port 6667 uses AES-ECB with UDP_KEY (regardless of device version)
                    decrypted = decrypt_payload(data, UDP_KEY, "3.3")
                    response = json.loads(decrypted.decode("utf-8"))
                except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
                    _LOGGER.warning("Failed to decrypt port 6667 response from %s: %s", ip, e)
                    return
            else:
                # Port 6666 - try JSON first, then decrypt with local_key if provided
                try:
                    response = json.loads(data.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # If not JSON, might be encrypted with device local_key
                    if self.local_key:
                        _LOGGER.debug("Port 6666 response not JSON, attempting decryption with local_key")
                        try:
                            decrypted = decrypt_payload(data, self.local_key, "3.3")
                            response = json.loads(decrypted.decode("utf-8"))
                        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as e:
                            _LOGGER.warning("Failed to decrypt port 6666 response from %s: %s", ip, e)
                            return
                    else:
                        _LOGGER.warning("Received encrypted port 6666 response but no local_key provided")
                        return

            # Add discovered device
            self.discovery._add_device(response, ip)

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
    retries: int = 2,
) -> list[TuyaDevice]:
    """Convenience function to discover Tuya devices.

    Args:
        broadcast_address: Broadcast address for discovery
        timeout: Discovery timeout in seconds
        local_key: Optional local key for decrypting responses
        retries: Number of broadcast retries (default 2)

    Returns:
        List of discovered TuyaDevice objects
    """
    discovery = TuyaDiscovery(
        broadcast_address=broadcast_address, timeout=timeout, retries=retries
    )
    return await discovery.discover(local_key)
