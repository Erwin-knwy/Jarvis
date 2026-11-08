// jarvis-brightness: [0-100]  -> prints current brightness (0-100), sets it if given.
// Uses DisplayServices (private, used by macOS itself); falls back to IOKit on older Intel Macs.
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <CoreGraphics/CoreGraphics.h>
#include <IOKit/graphics/IOGraphicsLib.h>

typedef int (*GetFn)(CGDirectDisplayID, float *);
typedef int (*SetFn)(CGDirectDisplayID, float);

static int iokit(float *get, float set, int doSet) {
    io_iterator_t it; io_object_t svc; int ok = 0;
    if (IOServiceGetMatchingServices(kIOMasterPortDefault, IOServiceMatching("IODisplayConnect"), &it) != KERN_SUCCESS) return 0;
    while ((svc = IOIteratorNext(it))) {
        if (doSet) ok |= IODisplaySetFloatParameter(svc, kNilOptions, CFSTR(kIODisplayBrightnessKey), set) == KERN_SUCCESS;
        else ok |= IODisplayGetFloatParameter(svc, kNilOptions, CFSTR(kIODisplayBrightnessKey), get) == KERN_SUCCESS;
        IOObjectRelease(svc);
    }
    IOObjectRelease(it);
    return ok;
}

int main(int argc, char **argv) {
    CGDirectDisplayID d = CGMainDisplayID();
    void *h = dlopen("/System/Library/PrivateFrameworks/DisplayServices.framework/DisplayServices", RTLD_LAZY);
    GetFn get = h ? (GetFn)dlsym(h, "DisplayServicesGetBrightness") : NULL;
    SetFn set = h ? (SetFn)dlsym(h, "DisplayServicesSetBrightness") : NULL;
    if (argc > 1) {
        float v = atof(argv[1]) / 100.0f;
        if (v < 0) v = 0; if (v > 1) v = 1;
        int ok = (set && set(d, v) == 0) || iokit(NULL, v, 1);
        if (!ok) { fprintf(stderr, "could not set brightness\n"); return 1; }
    }
    float cur = -1;
    if (!(get && get(d, &cur) == 0)) iokit(&cur, 0, 0);
    if (cur < 0) { fprintf(stderr, "could not read brightness\n"); return 1; }
    printf("%d\n", (int)(cur * 100 + 0.5f));
    return 0;
}
