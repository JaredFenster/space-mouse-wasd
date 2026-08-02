#include "CGShim.h"

#include <dlfcn.h>
#include <CoreFoundation/CoreFoundation.h>

/* These live in CoreGraphics but are not in any public header, so we resolve
 * them at runtime via dlsym rather than link against them directly — exactly
 * what the Python controller did with ctypes. */
typedef int (*CGSDefaultConnectionFn)(void);
typedef int (*CGSSetConnectionPropertyFn)(int cid, int targetCID,
                                          const void *key, const void *value);

bool smw_allow_background_cursor_hiding(void) {
    void *cg = dlopen(
        "/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics",
        RTLD_LAZY);
    if (!cg) {
        return false;
    }

    CGSDefaultConnectionFn defaultConnection =
        (CGSDefaultConnectionFn)dlsym(cg, "_CGSDefaultConnection");
    CGSSetConnectionPropertyFn setConnectionProperty =
        (CGSSetConnectionPropertyFn)dlsym(cg, "CGSSetConnectionProperty");
    if (!defaultConnection || !setConnectionProperty) {
        return false;
    }

    int conn = defaultConnection();
    CFStringRef key = CFStringCreateWithCString(
        NULL, "SetsCursorInBackground", kCFStringEncodingUTF8);
    if (!key) {
        return false;
    }
    setConnectionProperty(conn, conn, key, kCFBooleanTrue);
    CFRelease(key);
    return true;
}
