FanBridge‑Link Firmware
=======================

Manual install only: the FanBridge app does not flash firmware for you. Install this onto your microcontroller yourself.

Install prebuilt (RP2040)
-------------------------

- Download UF2 (v1.0.0): https://github.com/RoBro92/fanbridge-link/releases/download/v1.0.0/fanbridge-link-rp2040-1.0.0.uf2
- Other versions/boards: https://github.com/RoBro92/fanbridge-link/releases
- Put the board into BOOTSEL (hold BOOTSEL while plugging in USB).
- A `RPI-RP2` drive appears. Copy the UF2 to it and wait for reboot.

Edit and rebuild (optional)
---------------------------

- Open `firmware/rp2040/FanBridge_Link/FanBridge_Link.ino` in Arduino IDE.
- Install “Raspberry Pi RP2040 Boards (by Earle Philhower)”. Board: “Raspberry Pi Pico”.
- Tweak settings at the top of the file (e.g., `GATE_PIN`, PWM values).
- Sketch → Export compiled Binary. Copy the generated `.uf2` to `RPI-RP2`.

Arduino CLI (alternative)
-------------------------

- `arduino-cli core update-index --additional-urls https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json`
- `arduino-cli core install rp2040:rp2040 --additional-urls https://github.com/earlephilhower/arduino-pico/releases/download/global/package_rp2040_index.json`
- `arduino-cli compile --fqbn rp2040:rp2040:rpipico --export-binaries firmware/rp2040/FanBridge_Link`

Versioning notes
----------------

- Release assets are named `fanbridge-link-<board>-<version>.uf2` (for example, `fanbridge-link-rp2040-1.0.0.uf2`).
- In the future, separate variants (e.g., different Arduino cores/boards) will publish as their own assets per release/tag.
