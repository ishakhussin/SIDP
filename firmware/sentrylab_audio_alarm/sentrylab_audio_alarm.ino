#include <Arduino.h>
#include "DFRobotDFPlayerMini.h"

// ESP32 UART2 connection to DFPlayer Mini.
#define DFP_RX_PIN 26
#define DFP_TX_PIN 27

constexpr uint32_t HOST_BAUD_RATE = 115200;
constexpr uint32_t DFP_BAUD_RATE = 9600;
constexpr uint8_t ALARM_TRACK = 1;
constexpr uint8_t ALARM_VOLUME = 25;
constexpr uint32_t HOST_TIMEOUT_MS = 5000;
constexpr uint32_t PLAYER_RETRY_MS = 5000;

HardwareSerial dfpSerial(2);
DFRobotDFPlayerMini player;

String commandBuffer;
bool playerReady = false;
bool alarmRequested = false;
bool alarmPlaying = false;
uint32_t lastHostCommandMs = 0;
uint32_t lastPlayerAttemptMs = 0;

bool connectPlayer() {
  lastPlayerAttemptMs = millis();
  if (!player.begin(dfpSerial, false, false)) {
    playerReady = false;
    Serial.println("AUDIO:OFFLINE");
    return false;
  }
  player.volume(ALARM_VOLUME);
  playerReady = true;
  Serial.print("AUDIO:ONLINE:FILES=");
  Serial.println(player.readFileCounts());
  return true;
}

void applyAlarmOutput() {
  if (!playerReady) {
    alarmPlaying = false;
    return;
  }
  if (alarmRequested && !alarmPlaying) {
    player.loop(ALARM_TRACK);
    alarmPlaying = true;
    Serial.println("STATE:ALARM_PLAYING");
  } else if (!alarmRequested && alarmPlaying) {
    player.stop();
    alarmPlaying = false;
    Serial.println("STATE:ALARM_STOPPED");
  }
}

void handleCommand(String command) {
  command.trim();
  if (command.length() == 0) {
    return;
  }
  lastHostCommandMs = millis();

  if (command == "HEARTBEAT") {
    Serial.println("ACK:HEARTBEAT");
    return;
  }
  if (command == "ALARM_OFF") {
    alarmRequested = false;
    applyAlarmOutput();
    Serial.println("ACK:ALARM_OFF");
    return;
  }
  if (command == "ALARM_ON" || command.startsWith("ALARM_ON:")) {
    alarmRequested = true;
    applyAlarmOutput();
    Serial.println("ACK:ALARM_ON");
    return;
  }
  if (command == "STATUS") {
    Serial.print("STATUS:PLAYER=");
    Serial.print(playerReady ? "ONLINE" : "OFFLINE");
    Serial.print(":ALARM=");
    Serial.println(alarmPlaying ? "ON" : "OFF");
    return;
  }
  Serial.println("ERROR:UNKNOWN_COMMAND");
}

void readHostCommands() {
  while (Serial.available() > 0) {
    const char incoming = static_cast<char>(Serial.read());
    if (incoming == '\n') {
      handleCommand(commandBuffer);
      commandBuffer = "";
    } else if (incoming != '\r' && commandBuffer.length() < 240) {
      commandBuffer += incoming;
    }
  }
}

void setup() {
  Serial.begin(HOST_BAUD_RATE);
  dfpSerial.begin(DFP_BAUD_RATE, SERIAL_8N1, DFP_RX_PIN, DFP_TX_PIN);
  commandBuffer.reserve(256);
  delay(500);

  Serial.println("READY:SENTRYLAB_AUDIO_V1");
  connectPlayer();
  // Deliberately do not play audio during boot; alarms require a laptop command.
}

void loop() {
  readHostCommands();

  if (alarmRequested && millis() - lastHostCommandMs > HOST_TIMEOUT_MS) {
    alarmRequested = false;
    applyAlarmOutput();
    Serial.println("STATE:HOST_TIMEOUT");
  }

  if (!playerReady && millis() - lastPlayerAttemptMs >= PLAYER_RETRY_MS) {
    connectPlayer();
    applyAlarmOutput();
  }

  delay(10);
}
