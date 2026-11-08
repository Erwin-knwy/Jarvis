// jarvis-bluetooth: on | off | status   (same private API blueutil uses)
#import <Foundation/Foundation.h>
#import <IOBluetooth/IOBluetooth.h>
int IOBluetoothPreferenceGetControllerPowerState(void);
void IOBluetoothPreferenceSetControllerPowerState(int state);

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2 || strcmp(argv[1], "status") == 0) {
            printf("%s\n", IOBluetoothPreferenceGetControllerPowerState() ? "on" : "off");
            return 0;
        }
        int want = strcmp(argv[1], "on") == 0 ? 1 : strcmp(argv[1], "off") == 0 ? 0 : -1;
        if (want < 0) { fprintf(stderr, "usage: jarvis-bluetooth on|off|status\n"); return 2; }
        IOBluetoothPreferenceSetControllerPowerState(want);
        for (int i = 0; i < 30 && IOBluetoothPreferenceGetControllerPowerState() != want; i++) usleep(100000);
        printf("%s\n", IOBluetoothPreferenceGetControllerPowerState() ? "on" : "off");
        return IOBluetoothPreferenceGetControllerPowerState() == want ? 0 : 1;
    }
}
