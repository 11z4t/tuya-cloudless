"""Extra unit tests for lib/tuya_cloudless/ble_provision.

Targets previously uncovered paths:
  - _reassemble_chunks chunk-index gap error (line 270)
  - BleProvisioner.__aenter__ / __aexit__ (468-470, 474, 478)
  - BleProvisioner.scan() — bleak missing, device found, device not found (494-516)
  - BleProvisioner.provision() — full sequence, timeout, fail, bad ACK, OSError (544-616)
  - BleProvisioner.disconnect() — connected, not connected, no client (620-629)
"""

from __future__ import annotations

import asyncio
import inspect
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_REPO = Path(__file__).parent.parent.parent
_LIB = str(_REPO / "lib")
if _LIB not in sys.path:
    sys.path.insert(0, _LIB)

from tuya_cloudless.ble_provision import (  # noqa: E402
    CMD_PAIR_FAIL,
    CMD_PAIR_SUCCESS,
    CMD_WIFI_CONFIG_RESP,
    BleFrame,
    BleProvisioner,
    ProvisionPayload,
    _chunk_frame,
    _reassemble_chunks,
)
from tuya_cloudless.exceptions import PairingError  # noqa: E402

# ── Helpers ────────────────────────────────────────────────────────────────────


def _make_payload() -> ProvisionPayload:
    return ProvisionPayload(
        ssid="TestNet",
        password="testpass",
        token=ProvisionPayload.generate_token(),
        activator_url="http://192.168.1.1:8099",
    )


def _make_ack_chunks(cmd: int) -> list[bytes]:
    """Build a valid single-chunk BLE ACK frame for the given command code."""
    frame = BleFrame(seq=2, cmd=cmd, payload=b"")
    return _chunk_frame(frame.encode())


def _make_handshake_resp_chunks(device_nonce: bytes) -> list[bytes]:
    """Build a valid handshake-response frame carrying device_nonce."""
    frame = BleFrame(seq=0, cmd=0x00, payload=device_nonce)
    return _chunk_frame(frame.encode())


# ── _reassemble_chunks chunk-gap error ────────────────────────────────────────


class TestReassembleChunkGap:
    def test_out_of_sequence_index_raises(self) -> None:
        """A chunk whose header index doesn't match its sorted position raises PairingError."""
        # Build a 4-chunk sequence, then replace chunk[2]'s index with 4 (out of range)
        # so the sorted order has a gap: indices [0, 1, 3, 4] → position 2 expects idx=2, gets 3.
        frame = BleFrame(seq=0, cmd=0x01, payload=b"x" * 60).encode()
        chunks = _chunk_frame(frame)
        assert len(chunks) == 4, "Expected 4 chunks for this test payload"

        total = chunks[0][1]  # == 4
        # Replace chunk[2]'s index from 2 to 4 — gives unique indices [0, 1, 4, 3]
        # After sort: [0, 1, 3, 4] — position 2 expects idx=2, gets 3
        bad_chunk = bytes([4, total]) + chunks[2][2:]
        tampered = [chunks[0], chunks[1], bad_chunk, chunks[3]]
        with pytest.raises(PairingError, match="sequence gap"):
            _reassemble_chunks(tampered)


# ── BleProvisioner async context manager ──────────────────────────────────────


class TestBleProvisionerContextManager:
    async def test_aenter_returns_self(self) -> None:
        provisioner = BleProvisioner()
        result = await provisioner.__aenter__()
        assert result is provisioner

    async def test_aexit_calls_disconnect(self) -> None:
        provisioner = BleProvisioner()
        with patch.object(provisioner, "disconnect", new_callable=AsyncMock) as mock_dc:
            await provisioner.__aexit__(None, None, None)
        mock_dc.assert_awaited_once()

    async def test_context_manager_calls_disconnect_on_exit(self) -> None:
        """Using 'async with' invokes disconnect on normal exit."""
        with patch.object(BleProvisioner, "disconnect", new_callable=AsyncMock) as mock_dc:
            async with BleProvisioner():
                pass
        mock_dc.assert_awaited_once()

    async def test_context_manager_calls_disconnect_on_exception(self) -> None:
        """Using 'async with' invokes disconnect even when body raises."""
        with (
            patch.object(BleProvisioner, "disconnect", new_callable=AsyncMock) as mock_dc,
            pytest.raises(ValueError),
        ):
            async with BleProvisioner():
                raise ValueError("test error")
        mock_dc.assert_awaited_once()


# ── BleProvisioner.scan ───────────────────────────────────────────────────────


def _make_fake_bleak_module(
    *,
    device: object = None,
    find_side_effect: object = None,
) -> MagicMock:
    """Build a fake ``bleak`` module with a BleakScanner that returns *device*."""
    captured_timeout: list[float] = []

    async def fake_find(filter_fn: object, timeout: float) -> object:
        captured_timeout.append(timeout)
        if find_side_effect is not None:
            raise find_side_effect  # type: ignore[misc]
        return device

    mock_scanner_cls = MagicMock()
    mock_scanner_cls.find_device_by_filter = fake_find
    mock_scanner_cls._captured_timeout = captured_timeout  # expose for assertions

    fake_bleak = MagicMock()
    fake_bleak.BleakScanner = mock_scanner_cls
    return fake_bleak


class TestBleProvisionerScan:
    async def test_raises_when_bleak_not_installed(self) -> None:
        """scan() raises PairingError if bleak cannot be imported."""
        provisioner = BleProvisioner()

        with (
            patch.dict("sys.modules", {"bleak": None}),
            pytest.raises(PairingError, match="bleak"),
        ):
            await provisioner.scan()

    async def test_returns_device_address_when_found(self) -> None:
        """scan() returns the address string of the discovered device."""
        mock_device = MagicMock()
        mock_device.address = "AA:BB:CC:DD:EE:FF"

        fake_bleak = _make_fake_bleak_module(device=mock_device)

        with patch.dict("sys.modules", {"bleak": fake_bleak}):
            provisioner = BleProvisioner()
            result = await provisioner.scan(timeout=5.0)

        assert result == "AA:BB:CC:DD:EE:FF"

    async def test_raises_when_no_device_found(self) -> None:
        """scan() raises PairingError when find_device_by_filter returns None."""
        fake_bleak = _make_fake_bleak_module(device=None)

        provisioner = BleProvisioner(scan_timeout=3.0)

        with (
            patch.dict("sys.modules", {"bleak": fake_bleak}),
            pytest.raises(PairingError, match="No Tuya BLE device found"),
        ):
            await provisioner.scan()

    async def test_uses_default_scan_timeout_when_none_given(self) -> None:
        """scan() uses self._scan_timeout when no timeout argument is passed."""
        fake_bleak = _make_fake_bleak_module(device=None)

        provisioner = BleProvisioner(scan_timeout=7.5)

        with patch.dict("sys.modules", {"bleak": fake_bleak}), pytest.raises(PairingError):
            await provisioner.scan()

        assert fake_bleak.BleakScanner._captured_timeout == [7.5]

    async def test_uses_explicit_timeout_when_given(self) -> None:
        """scan(timeout=X) overrides the default scan_timeout."""
        fake_bleak = _make_fake_bleak_module(device=None)

        provisioner = BleProvisioner(scan_timeout=10.0)

        with patch.dict("sys.modules", {"bleak": fake_bleak}), pytest.raises(PairingError):
            await provisioner.scan(timeout=2.0)

        assert fake_bleak.BleakScanner._captured_timeout == [2.0]


# ── BleProvisioner.provision ──────────────────────────────────────────────────


class _FakeBleakClient:
    """Real stub class used as BleakClient so isinstance() checks work.

    Provision tests instantiate this with a factory function that
    intercepts construction and returns a pre-configured mock.
    """


def _make_bleak_module_for_provision(
    mock_client: object,
) -> MagicMock:
    """Build a fake ``bleak`` module whose BleakClient constructor returns *mock_client*.

    Creates a real subclass of _FakeBleakClient so isinstance(mock_client, BleakClient)
    returns True inside provision()'s finally/disconnect path.
    """

    class _BleakClientCls(_FakeBleakClient):
        """Fake BleakClient: construction always returns the pre-built mock_client."""

        def __new__(cls, *args: object, **kwargs: object) -> object:  # type: ignore[misc]
            return mock_client

    fake_bleak = MagicMock()
    fake_bleak.BleakClient = _BleakClientCls
    fake_bleak.BleakGATTCharacteristic = MagicMock()

    return fake_bleak


def _make_provision_client(
    notify_chunks_sequence: list[list[bytes]],
) -> _FakeBleakClient:
    """Build a fake BleakClient that feeds notify_chunks_sequence in order.

    Each burst in notify_chunks_sequence is fired ONCE after the LAST chunk
    of the corresponding outgoing frame is written (i.e., when the write chunk
    index equals total - 1).  This matches how a real Tuya device responds:
    it sends its response frame after receiving the complete request frame.

    Returns a _FakeBleakClient subclass instance with class-level async CM support.
    """
    notify_callbacks: list[object] = []
    burst_index_box = [0]
    the_chunks = notify_chunks_sequence

    class _Client(_FakeBleakClient):
        is_connected = True

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, *args: object) -> bool:
            return False

        async def start_notify(self, char_uuid: str, callback: object) -> None:
            notify_callbacks.append(callback)

        async def write_gatt_char(
            self, char_uuid: str, data: bytes, response: bool = False
        ) -> None:
            # data is a single transport chunk: byte[0]=chunk_no, byte[1]=total
            chunk_no = data[0]
            total = data[1]
            is_last_chunk = chunk_no + 1 == total

            if is_last_chunk:
                # Full outgoing frame has been sent; fire the next response burst
                idx = burst_index_box[0]
                if notify_callbacks and idx < len(the_chunks):
                    burst = the_chunks[idx]
                    cb = notify_callbacks[-1]
                    for resp_chunk in burst:
                        ba = bytearray(resp_chunk)
                        cb(MagicMock(), ba)  # type: ignore[operator]
                    burst_index_box[0] += 1

        async def disconnect(self) -> None:
            pass

    return _Client()


def _make_error_client(side_effect: Exception) -> _FakeBleakClient:
    """Return a _FakeBleakClient whose __aenter__ raises *side_effect*."""

    class _ErrorClient(_FakeBleakClient):
        async def __aenter__(self) -> _ErrorClient:
            raise side_effect

        async def __aexit__(self, *args: object) -> bool:
            return False

    return _ErrorClient()


class TestBleProvisionerProvision:
    async def test_raises_when_bleak_not_installed(self) -> None:
        """provision() raises PairingError if bleak cannot be imported."""
        provisioner = BleProvisioner()

        with (
            patch.dict("sys.modules", {"bleak": None, "bleak.backends.characteristic": None}),
            pytest.raises(PairingError, match="bleak"),
        ):
            await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_successful_provision_wifi_config_resp(self) -> None:
        """Successful provisioning with CMD_WIFI_CONFIG_RESP ACK completes without error."""
        device_nonce = bytes(range(16))
        handshake_resp = _make_handshake_resp_chunks(device_nonce)
        wifi_ack = _make_ack_chunks(CMD_WIFI_CONFIG_RESP)

        mock_client = _make_provision_client([handshake_resp, wifi_ack])
        fake_bleak = _make_bleak_module_for_provision(mock_client)

        with patch.dict(
            "sys.modules",
            {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
        ):
            provisioner = BleProvisioner()
            await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_successful_provision_pair_success(self) -> None:
        """Successful provisioning with CMD_PAIR_SUCCESS ACK completes without error."""
        device_nonce = bytes(range(16))
        handshake_resp = _make_handshake_resp_chunks(device_nonce)
        pair_ok = _make_ack_chunks(CMD_PAIR_SUCCESS)

        mock_client = _make_provision_client([handshake_resp, pair_ok])
        fake_bleak = _make_bleak_module_for_provision(mock_client)

        with patch.dict(
            "sys.modules",
            {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
        ):
            provisioner = BleProvisioner()
            await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_provision_raises_on_pair_fail(self) -> None:
        """CMD_PAIR_FAIL from device raises PairingError."""
        device_nonce = bytes(range(16))
        handshake_resp = _make_handshake_resp_chunks(device_nonce)
        pair_fail = _make_ack_chunks(CMD_PAIR_FAIL)

        mock_client = _make_provision_client([handshake_resp, pair_fail])
        fake_bleak = _make_bleak_module_for_provision(mock_client)

        with patch.dict(
            "sys.modules",
            {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
        ):
            provisioner = BleProvisioner()
            with pytest.raises(PairingError, match="rejected WiFi config"):
                await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_provision_raises_on_unexpected_ack_cmd(self) -> None:
        """An unexpected ACK command code raises PairingError."""
        device_nonce = bytes(range(16))
        handshake_resp = _make_handshake_resp_chunks(device_nonce)
        bad_ack = _make_ack_chunks(0xFF)

        mock_client = _make_provision_client([handshake_resp, bad_ack])
        fake_bleak = _make_bleak_module_for_provision(mock_client)

        with patch.dict(
            "sys.modules",
            {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
        ):
            provisioner = BleProvisioner()
            with pytest.raises(PairingError, match="Unexpected ACK"):
                await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_provision_raises_on_oserror(self) -> None:
        """An OSError from bleak is wrapped into PairingError."""
        fake_bleak = _make_bleak_module_for_provision(
            _make_error_client(OSError("BLE I/O failure"))
        )

        with patch.dict(
            "sys.modules",
            {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
        ):
            provisioner = BleProvisioner()
            with pytest.raises(PairingError, match="BLE provisioning failed"):
                await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_provision_raises_on_runtime_error(self) -> None:
        """A RuntimeError from bleak is wrapped into PairingError."""
        fake_bleak = _make_bleak_module_for_provision(
            _make_error_client(RuntimeError("GATT error"))
        )

        with patch.dict(
            "sys.modules",
            {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
        ):
            provisioner = BleProvisioner()
            with pytest.raises(PairingError, match="BLE provisioning failed"):
                await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_provision_re_raises_pairing_error_directly(self) -> None:
        """PairingError from inner logic propagates unchanged."""
        fake_bleak = _make_bleak_module_for_provision(
            _make_error_client(PairingError("inner pairing error"))
        )

        with patch.dict(
            "sys.modules",
            {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
        ):
            provisioner = BleProvisioner()
            with pytest.raises(PairingError, match="inner pairing error"):
                await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_provision_clears_client_in_finally(self) -> None:
        """_client is set to None after provision() returns (success or failure)."""
        device_nonce = bytes(range(16))
        handshake_resp = _make_handshake_resp_chunks(device_nonce)
        wifi_ack = _make_ack_chunks(CMD_WIFI_CONFIG_RESP)

        mock_client = _make_provision_client([handshake_resp, wifi_ack])
        fake_bleak = _make_bleak_module_for_provision(mock_client)

        with patch.dict(
            "sys.modules",
            {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
        ):
            provisioner = BleProvisioner()
            await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

        assert provisioner._client is None

    async def test_provision_handshake_timeout_raises(self) -> None:
        """Timeout waiting for handshake response raises PairingError."""

        class _SilentClient(_FakeBleakClient):
            """Client that never fires notify callbacks — simulates no handshake response."""

            async def __aenter__(self) -> _SilentClient:
                return self

            async def __aexit__(self, *args: object) -> bool:
                return False

            async def start_notify(self, char_uuid: str, callback: object) -> None:
                pass

            async def write_gatt_char(
                self, char_uuid: str, data: bytes, response: bool = False
            ) -> None:
                pass

        fake_bleak = _make_bleak_module_for_provision(_SilentClient())

        def _timeout_and_close(coro: object, timeout: float) -> object:
            if inspect.iscoroutine(coro):
                coro.close()  # Prevent "coroutine was never awaited" RuntimeWarning
            raise TimeoutError

        with (
            patch.dict(
                "sys.modules",
                {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
            ),
            patch("asyncio.wait_for", side_effect=_timeout_and_close),
        ):
            provisioner = BleProvisioner()
            with pytest.raises(PairingError, match="Timeout waiting for BLE handshake"):
                await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload())

    async def test_provision_wifi_ack_timeout_raises(self) -> None:
        """Timeout waiting for WiFi config ACK raises PairingError."""
        device_nonce = bytes(range(16))
        handshake_resp = _make_handshake_resp_chunks(device_nonce)

        # Provide handshake response but NO wifi ack — second wait_for times out
        call_count = 0

        original_wait_for = asyncio.wait_for

        async def fake_wait_for(coro: object, timeout: float) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return await original_wait_for(coro, timeout=5.0)  # type: ignore[arg-type]
            # Close the coroutine before raising to prevent "never awaited" warning
            if inspect.iscoroutine(coro):
                coro.close()
            raise TimeoutError

        mock_client = _make_provision_client([handshake_resp])  # only 1 burst
        fake_bleak = _make_bleak_module_for_provision(mock_client)

        with (
            patch.dict(
                "sys.modules",
                {"bleak": fake_bleak, "bleak.backends.characteristic": fake_bleak},
            ),
            patch("asyncio.wait_for", side_effect=fake_wait_for),
        ):
            provisioner = BleProvisioner()
            with pytest.raises(PairingError, match="Timeout waiting for WiFi config ACK"):
                await provisioner.provision("AA:BB:CC:DD:EE:FF", _make_payload(), pair_timeout=0.1)


# ── BleProvisioner.disconnect ──────────────────────────────────────────────────


class TestBleProvisionerDisconnect:
    async def test_disconnect_when_no_client_is_noop(self) -> None:
        """disconnect() does nothing when _client is None."""
        provisioner = BleProvisioner()
        assert provisioner._client is None
        await provisioner.disconnect()  # must not raise

    async def test_disconnect_calls_client_disconnect_when_connected(self) -> None:
        """disconnect() calls client.disconnect() when client is connected."""
        # We need a real BleakClient class in sys.modules so isinstance works
        mock_client_instance = MagicMock()
        mock_client_instance.is_connected = True
        mock_client_instance.disconnect = AsyncMock()

        # Build a real class that the instance is actually an instance of
        class _FakeBleakClient:
            pass

        real_instance = _FakeBleakClient()
        real_instance.is_connected = True  # type: ignore[attr-defined]
        real_instance.disconnect = AsyncMock()  # type: ignore[attr-defined]

        fake_bleak = MagicMock()
        fake_bleak.BleakClient = _FakeBleakClient

        provisioner = BleProvisioner()
        provisioner._client = real_instance

        with patch.dict("sys.modules", {"bleak": fake_bleak}):
            await provisioner.disconnect()

        real_instance.disconnect.assert_awaited_once()
        assert provisioner._client is None

    async def test_disconnect_not_connected_skips_call(self) -> None:
        """disconnect() skips the BLE disconnect call when not connected."""

        class _FakeBleakClient:
            pass

        real_instance = _FakeBleakClient()
        real_instance.is_connected = False  # type: ignore[attr-defined]
        real_instance.disconnect = AsyncMock()  # type: ignore[attr-defined]

        fake_bleak = MagicMock()
        fake_bleak.BleakClient = _FakeBleakClient

        provisioner = BleProvisioner()
        provisioner._client = real_instance

        with patch.dict("sys.modules", {"bleak": fake_bleak}):
            await provisioner.disconnect()

        real_instance.disconnect.assert_not_awaited()
        assert provisioner._client is None

    async def test_disconnect_suppresses_oserror(self) -> None:
        """disconnect() suppresses OSError raised by bleak's disconnect."""

        class _FakeBleakClient:
            pass

        real_instance = _FakeBleakClient()
        real_instance.is_connected = True  # type: ignore[attr-defined]
        real_instance.disconnect = AsyncMock(side_effect=OSError("connection lost"))  # type: ignore[attr-defined]

        fake_bleak = MagicMock()
        fake_bleak.BleakClient = _FakeBleakClient

        provisioner = BleProvisioner()
        provisioner._client = real_instance

        with patch.dict("sys.modules", {"bleak": fake_bleak}):
            await provisioner.disconnect()  # must not raise

        assert provisioner._client is None

    async def test_non_bleak_client_object_clears_client(self) -> None:
        """disconnect() clears _client even when it's not a BleakClient instance."""
        provisioner = BleProvisioner()
        provisioner._client = object()  # Not a BleakClient

        class _FakeBleakClient:
            pass

        fake_bleak = MagicMock()
        fake_bleak.BleakClient = _FakeBleakClient

        with patch.dict("sys.modules", {"bleak": fake_bleak}):
            await provisioner.disconnect()

        assert provisioner._client is None

    async def test_disconnect_via_context_manager_exit(self) -> None:
        """__aexit__ triggers disconnect; _client is cleared."""
        provisioner = BleProvisioner()
        mock_dc = AsyncMock()

        with patch.object(provisioner, "disconnect", mock_dc):
            await provisioner.__aexit__(None, None, None)

        mock_dc.assert_awaited_once()
