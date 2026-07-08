# FanBridge-Link Firmware

The FanBridge application does not flash firmware for you. You must manually install the firmware onto your FanBridge Link microcontroller.

## Installing the Prebuilt Firmware (RP2040)

To easily flash your FanBridge Link hardware:

1. **Download UF2 Firmware**: [Download v1.0.0](https://github.com/RoBro92/fanbridge-link/releases/download/v1.0.0/fanbridge-link-rp2040-1.0.0.uf2) or check for [newer versions](https://github.com/RoBro92/fanbridge-link/releases).
2. **Enter BOOTSEL Mode**: Hold the `BOOTSEL` button on your RP2040 board while plugging it in via USB.
3. **Flash**: An `RPI-RP2` mass storage drive will appear on your computer. Copy the downloaded `.uf2` file to this drive.
4. **Reboot**: The board will automatically reboot and run the firmware.

## Versioning Notes

- Release assets follow the naming convention: `fanbridge-link-<board>-<version>.uf2` (e.g., `fanbridge-link-rp2040-1.0.0.uf2`).
- Separate hardware variants will be published as distinct assets in the releases tab.
