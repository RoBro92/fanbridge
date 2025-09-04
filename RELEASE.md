Version: 0.2.0-dev

# Changelog

## 0.2.0-dev — 2025-09-05

- Added serial communication support for Arduino/RP2040 (with test commands).
- Implemented detection and configuration of USB serial devices via template.xml.
- Added logging improvements for serial and API errors in docker logs.
- Improved UI pills layout: combined Change Password, Logout, Theme toggle into a single dropdown menu.
- Made "Last update" a pill and added tooltips explaining refresh/disks.ini times.
- Themed modals for reset to defaults and refresh interval to match UI.
- Added Reset to Defaults button for overrides and fan curves.
- Integrated unsaved changes and validation error pills into configuration panel.

## 0.1.1-dev — 2025-09-04

- Added CSRF token meta tag in index.html for security.
- Enhanced styling for warning and error messages in index.html.
- Implemented a refresh interval modal in index.html to allow users to set polling frequency.
- Added functionality for changing user password and logging out in index.html.
- Created a reset to defaults modal for restoring fan curves and overrides in index.html.
- Added Login screen and Session Cookies
- Added enforcement and thersholds on PWM values

## 0.1.0-dev — 2025-09-04
- Introduce single release file with version + changelog.
- Wire Flask app to read version from this file.


