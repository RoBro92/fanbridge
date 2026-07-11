# FanBridge-Link Firmware

## Updating the Firmware

There are two ways to update the RP2040 firmware: via the FanBridge web UI (requires a privileged container), or manually from the Unraid terminal.

### Method 1: In-App Update (Privileged Container)

If your FanBridge Docker container is running in **privileged mode** (or with `CAP_SYS_ADMIN`), you can update the firmware directly from the web UI:

1. Navigate to the **Serial** tab → **Link Updates** panel.
2. Click **Install latest** to flash the latest version from the configured repo, or use **Choose file… → Flash file** to upload a custom `.uf2` build.
3. The app will automatically send the `BOOTSEL` command, detect the RP2 boot volume, copy the firmware, and verify the update.

> **Note:** This method also requires `/dev/bus/usb` and `/dev/disk/by-label` to be mapped into the container. These are available as advanced settings in the Unraid template.

### Method 2: Unraid Terminal (No Privileged Container Required)

If your container is **not** privileged, you can update the firmware directly from the Unraid terminal (SSH or web terminal). This is the recommended approach for most users.

#### Quick update (one-liner)

```bash
# Download the latest UF2, trigger BOOTSEL via serial, and copy to the RP2 volume
UF2_URL="https://github.com/RoBro92/fanbridge/releases/download/fw-v2.0.0/fanbridge-rp2040-2.0.0.uf2" && \
UF2_FILE="/tmp/fanbridge.uf2" && \
wget -O "$UF2_FILE" "$UF2_URL" && \
echo "BOOTSEL" > /dev/ttyACM0 && \
sleep 5 && \
MOUNT=$(lsblk -o NAME,LABEL -rn | grep RPI-RP2 | awk '{print "/dev/"$1}') && \
mkdir -p /tmp/rp2 && mount "$MOUNT" /tmp/rp2 && \
cp "$UF2_FILE" /tmp/rp2/ && sync && \
umount /tmp/rp2 && rm -rf /tmp/rp2 "$UF2_FILE" && \
echo "Done! Firmware updated."
```

#### Step-by-step

```bash
# 1. Download the firmware UF2 file
wget -O /tmp/fanbridge.uf2 \
  "https://github.com/RoBro92/fanbridge/releases/download/fw-v2.0.0/fanbridge-rp2040-2.0.0.uf2"

# 2. Send BOOTSEL command to reboot the RP2040 into bootloader mode
#    (adjust the serial port path if yours differs)
echo "BOOTSEL" > /dev/ttyACM0

# 3. Wait for the RP2040 to re-enumerate as a mass-storage device (~3-5 seconds)
sleep 5

# 4. Find and mount the RPI-RP2 volume
#    The device will appear as a removable drive labelled "RPI-RP2"
MOUNT_DEV=$(lsblk -o NAME,LABEL -rn | grep RPI-RP2 | awk '{print "/dev/"$1}')
echo "Found RP2 device: $MOUNT_DEV"
mkdir -p /tmp/rp2
mount "$MOUNT_DEV" /tmp/rp2

# 5. Copy the UF2 file — the RP2040 will auto-flash and reboot
cp /tmp/fanbridge.uf2 /tmp/rp2/
sync

# 6. Clean up
umount /tmp/rp2
rm -rf /tmp/rp2 /tmp/fanbridge.uf2

# 7. Verify — after a few seconds the serial port will reappear
sleep 5
echo "VERSION" > /dev/ttyACM0
# You should see the new version number in the FanBridge Serial console
```

#### Troubleshooting

| Problem | Solution |
|---|---|
| `/dev/ttyACM0` not found | Check `ls /dev/ttyACM*` — the port may have a different number. Also check `/dev/serial/by-id/` for stable names. |
| `RPI-RP2` volume not appearing | Hold the physical BOOTSEL button on the board while re-plugging USB to force bootloader mode. |
| `lsblk` doesn't show `RPI-RP2` | Try `ls /dev/disk/by-label/` to check if the label is visible. The volume may take a few seconds to appear after the BOOTSEL command. |
| Permission denied on mount | Run the commands as root (`sudo`) or from the Unraid web terminal which runs as root by default. |

## First-Time Installation

For a brand-new RP2040 board that doesn't have FanBridge firmware yet:

1. **Download UF2 Firmware**: [Download v2.0.0](https://github.com/RoBro92/fanbridge/releases/download/fw-v2.0.0/fanbridge-rp2040-2.0.0.uf2) or check for [newer versions](https://github.com/RoBro92/fanbridge/releases).
2. **Enter BOOTSEL Mode**: Hold the `BOOTSEL` button on your RP2040 board while plugging it in via USB.
3. **Flash**: An `RPI-RP2` mass storage drive will appear on your computer. Copy the downloaded `.uf2` file to this drive.
4. **Reboot**: The board will automatically reboot and run the firmware.

## Versioning Notes

- Release assets follow the naming convention: `fanbridge-<board>-<version>.uf2` (e.g., `fanbridge-rp2040-1.0.0.uf2`).
- Separate hardware variants will be published as distinct assets in the releases tab.
