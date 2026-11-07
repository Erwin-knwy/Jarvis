#!/usr/bin/env bash
# Downloads JDK 17, the Android SDK (platform 34) and Gradle 8.7 into ~/.jarvis-toolchain (no sudo).
set -e
cd ~/.jarvis-toolchain
if [ ! -d jdk ]; then
  curl -sSL --retry 8 --retry-all-errors --retry-delay 3 -C - -o jdk.tgz "https://api.adoptium.net/v3/binary/latest/17/ga/mac/x64/jdk/hotspot/normal/eclipse"
  mkdir jdk-tmp && tar -xzf jdk.tgz -C jdk-tmp && mv jdk-tmp/*/Contents/Home jdk && rm -rf jdk-tmp jdk.tgz
fi
echo "JDK: $(jdk/bin/java -version 2>&1 | head -1)"
if [ ! -d android-sdk/cmdline-tools/latest ]; then
  curl -sSL --retry 8 --retry-all-errors --retry-delay 3 -C - -o clt.zip "https://dl.google.com/android/repository/commandlinetools-mac-11076708_latest.zip"
  mkdir -p android-sdk/cmdline-tools && unzip -q clt.zip -d android-sdk/cmdline-tools && mv android-sdk/cmdline-tools/cmdline-tools android-sdk/cmdline-tools/latest && rm clt.zip
fi
export JAVA_HOME=~/.jarvis-toolchain/jdk ANDROID_HOME=~/.jarvis-toolchain/android-sdk
yes | android-sdk/cmdline-tools/latest/bin/sdkmanager --licenses > /dev/null 2>&1 || true
android-sdk/cmdline-tools/latest/bin/sdkmanager "platforms;android-34" "build-tools;34.0.0" "platform-tools" > sdk.log 2>&1
echo "SDK: $(ls android-sdk)"
if [ ! -d gradle ]; then
  curl -sSL --retry 8 --retry-all-errors --retry-delay 3 -C - -o gradle.zip "https://services.gradle.org/distributions/gradle-8.7-bin.zip"
  unzip -q gradle.zip && mv gradle-8.7 gradle && rm gradle.zip
fi
echo "Gradle: $(JAVA_HOME=$JAVA_HOME gradle/bin/gradle --version 2>/dev/null | grep '^Gradle')"
echo DONE
