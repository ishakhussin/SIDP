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
