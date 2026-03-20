"""Cross-platform Bluetooth headset detection.

Detects whether the current audio input device is a Bluetooth headset
by querying OS-level Bluetooth device metadata (Class of Device).

macOS: system_profiler SPBluetoothDataType -json
Windows: WinRT Windows.Devices.Bluetooth API (sandbox-safe, no subprocess)
"""

from __future__ import annotations

import asyncio
import json
import logging
import platform
import subprocess

log = logging.getLogger(__name__)

_HEADSET_MINOR_TYPES_MAC = {"Headset", "Hands-Free", "Headphones"}

# Bluetooth Class of Device — Audio/Video major class (0x04)
# Minor classes: 0x01=Headset, 0x02=Hands-Free, 0x06=Headphones
_BT_MAJOR_AUDIO = 0x04
_BT_MINOR_HEADSET = 0x01
_BT_MINOR_HANDSFREE = 0x02
_BT_MINOR_HEADPHONES = 0x06
_HEADSET_MINOR_CLASSES = {_BT_MINOR_HEADSET, _BT_MINOR_HANDSFREE, _BT_MINOR_HEADPHONES}


def is_bluetooth_headset(device_id: str) -> bool:
    """Check if a QMediaDevices audio input ID corresponds to a Bluetooth headset.

    Args:
        device_id: The decoded bytes from QMediaDevices.defaultAudioInput().id().
                   macOS format: "00-25-52-0B-38-03:input"
                   Windows format varies by driver.

    Returns:
        True if the device matches a connected Bluetooth headset/headphones.
        False if not a headset, not Bluetooth, or detection fails.
    """
    system = platform.system()
    try:
        if system == "Darwin":
            return _detect_macos(device_id)
        elif system == "Windows":
            return _detect_windows(device_id)
        else:
            return False
    except Exception:
        log.debug("Bluetooth headset detection failed", exc_info=True)
        return False


def _detect_macos(device_id: str) -> bool:
    """macOS: match device_id MAC address against system_profiler Bluetooth data."""
    # Extract MAC from QMediaDevices ID (format: "00-25-52-0B-38-03:input")
    mac_from_id = device_id.split(":")[0].strip().lower() if ":" in device_id else ""
    if not mac_from_id or "builtin" in device_id.lower():
        return False

    result = subprocess.run(
        ["system_profiler", "SPBluetoothDataType", "-json"],
        capture_output=True, text=True, timeout=5,
    )
    if result.returncode != 0:
        return False

    data = json.loads(result.stdout)
    bt_entries = data.get("SPBluetoothDataType", [])

    for entry in bt_entries:
        connected = entry.get("device_connected", [])
        for device_dict in connected:
            for _name, props in device_dict.items():
                # Normalize address: colons → dashes, lowercase
                addr = props.get("device_address", "")
                addr_normalized = addr.replace(":", "-").lower()
                if addr_normalized == mac_from_id:
                    minor_type = props.get("device_minorType", "")
                    if minor_type in _HEADSET_MINOR_TYPES_MAC:
                        log.debug("Bluetooth headset detected: %s (%s)", _name, minor_type)
                        return True
                    log.debug("Bluetooth device matched but not headset: %s (%s)", _name, minor_type)
                    return False
    return False


def _detect_windows(device_id: str) -> bool:
    """Windows: query connected Bluetooth devices via WinRT API."""
    try:
        return asyncio.run(_detect_windows_async(device_id))
    except Exception:
        log.debug("WinRT Bluetooth detection failed", exc_info=True)
        return False


async def _detect_windows_async(device_id: str) -> bool:
    """Async implementation using WinRT Bluetooth APIs."""
    from winrt.windows.devices.bluetooth import (
        BluetoothConnectionStatus,
        BluetoothDevice,
    )
    from winrt.windows.devices.enumeration import DeviceInformation

    # Get AQS selector for connected Bluetooth devices
    selector = BluetoothDevice.get_device_selector_from_connection_status(
        BluetoothConnectionStatus.CONNECTED
    )
    devices = await DeviceInformation.find_all_async(selector)

    device_id_lower = device_id.lower()

    for i in range(devices.size):
        dev_info = devices.get_at(i)
        name = dev_info.name or ""
        if not name or name.lower() not in device_id_lower:
            continue

        # Found a BT device whose name appears in the audio device ID —
        # get full BluetoothDevice to inspect Class of Device
        bt_dev = await BluetoothDevice.from_id_async(dev_info.id)
        if bt_dev is None:
            continue

        cod = bt_dev.class_of_device
        if cod is None:
            bt_dev.close()
            continue

        major_class = cod.major_class.value if hasattr(cod.major_class, 'value') else int(cod.major_class)
        minor_class = cod.minor_class.value if hasattr(cod.minor_class, 'value') else int(cod.minor_class)

        if major_class == _BT_MAJOR_AUDIO and minor_class in _HEADSET_MINOR_CLASSES:
            log.debug("Bluetooth headset detected: %s (major=%d minor=%d)", name, major_class, minor_class)
            bt_dev.close()
            return True

        log.debug("Bluetooth device matched but not headset: %s (major=%d minor=%d)", name, major_class, minor_class)
        bt_dev.close()
        return False

    return False
