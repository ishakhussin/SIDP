# SentryLab ESP32 Audio Alarm

This firmware receives newline-delimited commands from the SentryLab laptop over USB serial at 115200 baud and controls a DFPlayer Mini over ESP32 UART2 at 9600 baud.

## Wiring

| ESP32 | DFPlayer Mini |
|---|---|
| GPIO 27 (TX2) | RX through a 1 kΩ resistor |
| GPIO 26 (RX2) | TX |
| 5V | VCC |
| GND | GND |

Connect the speaker to `SPK1` and `SPK2`. The ESP32, DFPlayer, and power supply must share ground. Use a stable supply appropriate for the selected speaker volume.

## MicroSD card

Format the card as FAT32 and copy `0001.mp3` as the first and only track during initial testing. The firmware loops track 1 while an alarm is required.

## Upload

### Visual Studio Code with PlatformIO

1. Install the PlatformIO IDE extension.
2. Open this `firmware/sentrylab_audio_alarm` folder as the VS Code workspace.
3. PlatformIO reads `platformio.ini` and installs ESP32 support plus `DFRobotDFPlayerMini` automatically.
4. Select the detected ESP32 COM port and choose **Upload**.
5. Close the serial monitor before starting SentryLab.

### Arduino IDE

1. Install ESP32 board support in Arduino IDE.
2. Install the `DFRobotDFPlayerMini` library.
3. Open `sentrylab_audio_alarm.ino`.
4. Select the ESP32 board and its COM port.
5. Upload the firmware and close Arduino Serial Monitor.

The firmware does not play a sound on boot. It accepts:

```text
HEARTBEAT
ALARM_ON:CAM 02:restricted_zone
ALARM_OFF
STATUS
```

If laptop commands stop for five seconds, the ESP32 stops the alarm rather than sounding indefinitely.

At boot, `AUDIO:ONLINE:FILES=1` confirms that the DFPlayer can read the card. `AUDIO:SD_ERROR:FILES=0` or a negative file count means the card is empty, unreadable, incorrectly formatted, or not seated correctly. The firmware will not claim that audio is online in this state.
