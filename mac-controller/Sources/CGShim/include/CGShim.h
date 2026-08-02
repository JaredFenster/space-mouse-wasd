#ifndef CGSHIM_H
#define CGSHIM_H

#include <stdbool.h>

/*
 * CGDisplayHideCursor normally only takes effect while the *calling* app is
 * frontmost — but here Fusion is frontmost, not us. Setting the window
 * server's "SetsCursorInBackground" connection property opts us out of that,
 * so the cursor can stay hidden while Fusion has focus.
 *
 * This pokes the same private CoreGraphics/SkyLight symbols the Python
 * controller reached through ctypes. Returns true on success.
 */
bool smw_allow_background_cursor_hiding(void);

#endif /* CGSHIM_H */
