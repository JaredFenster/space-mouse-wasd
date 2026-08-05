# SpaceMouseWASD - spacemouse-style smooth fly/orbit navigation for Fusion.
# Listens on localhost UDP for motion packets from controller.py (the companion
# script that hooks CapsLock + WASD + mouse) and drives the viewport camera
# with velocity smoothing so motion glides like a real spacemouse.
#
# All tuning knobs are at the top. Camera units are cm (Fusion API internal).

import adsk.core
import adsk.fusion
import traceback
import threading
import socket
import json
import math
import time

# ---------------------------------------------------------------- tuning ----
UDP_PORT = 42737          # must match controller.py
PAN_SPEED = 1.4           # pan speed, in "view heights per second" at full deflection
DOLLY_SPEED = 1.6         # zoom rate (exponential) per second holding W/S
ORBIT_SENS = 0.005        # radians of orbit per pixel of mouse movement
BOOST_MULT = 3.0          # Shift multiplier
TAU_MOVE = 0.12           # smoothing time-constant for pan/zoom (bigger = floatier)
TAU_ORBIT = 0.06          # smoothing time-constant for orbit
WHEEL_ZOOM_KICK = 0.7     # zoom impulse per wheel notch (scroll-zoom mode)
WHEEL_TAU = 0.15          # how quickly a wheel-zoom impulse glides out
INVERT_WHEEL_ZOOM = False  # False: scroll up = zoom in
MIN_DIST = 0.05           # closest allowed eye-to-target distance, cm
INVERT_ORBIT_X = False    # flip horizontal orbit direction
INVERT_ORBIT_Y = False    # flip vertical orbit direction
INVERT_ZOOM = False       # flip W/S
ORBIT_UP_AXIS = 'auto'    # turntable axis: 'auto' | 'x' | 'y' | 'z'
                          # auto = level-horizon detection per document
                          # (see _modelUpAxis)
# ----------------------------------------------------------------------------

EVENT_ID = 'SpaceMouseWASD_navEvent'

_app = None
_ui = None
_handlers = []
_customEvent = None
_thread = None
_stop = threading.Event()
_sock = None
_lock = threading.Lock()
_latest = None
_pending = False


def _log(msg):
    try:
        _app.log('SpaceMouseWASD: ' + msg)
    except Exception:
        pass


def _prefUpIndex():
    try:
        pref = _app.preferences.generalPreferences.defaultModelingOrientation
        if pref == adsk.core.DefaultModelingOrientations.YUpModelingOrientation:
            return 1
    except Exception:
        pass
    return 2


_axisLatch = {'i': None, 's': 1.0}


def _modelUpAxis(camUp, fwd):
    """World 'up' used for turntable orbit.

    'auto' uses level-horizon detection with strong hysteresis. The latched
    axis is kept while the horizon is level around it (camera right vector
    perpendicular to it). It switches only when the latch is clearly
    violated AND another axis is both level and strongly up-aligned - i.e.
    a genuinely mis-oriented document. Both halves of that condition
    matter: picking the axis nearest the camera's up (1.3.1) flipped
    sideways in pitched views, and switching to any merely-level axis
    (1.3.3) let views left rolled by Look At / sketch edits latch a
    sideways axis for a whole fly session - intermittent 'free orbit'."""
    comps = (camUp.x, camUp.y, camUp.z)
    if ORBIT_UP_AXIS in ('x', 'y', 'z'):
        i = 'xyz'.index(ORBIT_UP_AXIS)
    else:
        if _axisLatch['i'] is None:
            _axisLatch['i'] = _prefUpIndex()
        i = _axisLatch['i']
        right = fwd.crossProduct(camUp)
        if right.length > 1e-9:
            right.normalize()
            rc = (abs(right.x), abs(right.y), abs(right.z))
            if rc[i] >= 0.2:
                # Latch violated: the horizon is rolled against the latched
                # axis. A replacement must be BOTH level and strongly
                # up-aligned - a view left rolled by Look At or a sketch
                # edit is rolled against every axis, qualifies nothing, and
                # keeps the latch (first orbit input just re-levels it).
                cand = [n for n in range(3)
                        if rc[n] < 0.2 and abs(comps[n]) > 0.7]
                if cand:
                    pref = _prefUpIndex()
                    i = pref if pref in cand else max(
                        cand, key=lambda n: abs(comps[n]))
                    if i != _axisLatch['i']:
                        _log('orbit axis switched to %s' % 'xyz'[i])
                    _axisLatch['i'] = i
    if abs(comps[i]) >= 0.05:              # keep working upside-down, with
        _axisLatch['s'] = -1.0 if comps[i] < 0 else 1.0   # pole hysteresis
    v = [0.0, 0.0, 0.0]
    v[i] = _axisLatch['s']
    return adsk.core.Vector3D.create(*v)


_bboxCache = {'stale': True, 'pt': None}


def _modelCenter():
    """World-space center of the visible model's bounding box, computed once
    per fly session - occurrence bounding boxes are expensive enough that
    recomputing on a timer visibly hitches the camera on larger designs.
    Used as the spacemouse-style pivot anchor. None if nothing to measure."""
    if not _bboxCache['stale']:
        return _bboxCache['pt']
    t0 = time.monotonic()
    pt = None
    try:
        des = adsk.fusion.Design.cast(_app.activeProduct)
        if des:
            lo = [math.inf] * 3
            hi = [-math.inf] * 3
            def acc(bb):
                if not bb:
                    return
                mn, mx = bb.minPoint, bb.maxPoint
                for i, (a, b) in enumerate(((mn.x, mx.x), (mn.y, mx.y),
                                            (mn.z, mx.z))):
                    lo[i] = min(lo[i], a)
                    hi[i] = max(hi[i], b)
            root = des.rootComponent
            for b in root.bRepBodies:
                try:
                    if b.isVisible:
                        acc(b.boundingBox)
                except Exception:
                    pass
            occs = root.allOccurrences
            for i in range(min(occs.count, 200)):
                try:
                    occ = occs.item(i)
                    if occ.isVisible:
                        acc(occ.boundingBox)
                except Exception:
                    pass
            if lo[0] < hi[0]:
                pt = adsk.core.Point3D.create((lo[0] + hi[0]) / 2.0,
                                              (lo[1] + hi[1]) / 2.0,
                                              (lo[2] + hi[2]) / 2.0)
    except Exception:
        pt = None
    _bboxCache['stale'] = False
    _bboxCache['pt'] = pt
    ms = (time.monotonic() - t0) * 1000.0
    if ms > 50.0:
        _log('pivot bbox measured in %.0f ms' % ms)
    return pt


class NavEventHandler(adsk.core.CustomEventHandler):
    def __init__(self):
        super().__init__()
        self.vel = [0.0, 0.0, 0.0]      # smoothed tx, ty, tz (unitless -1..1-ish)
        self.orbVel = [0.0, 0.0]        # smoothed orbit rates, px/sec
        self.wheelVel = 0.0             # decaying wheel-zoom impulse
        self.lastT = None
        self.upAxis = None              # latched per fly session (see _applyCamera)

    def notify(self, args):
        global _pending
        try:
            with _lock:
                pkt = _latest
                _pending = False
            if pkt is None:
                return

            now = time.monotonic()
            gap = None if self.lastT is None else now - self.lastT
            self.lastT = now
            if gap is None or gap > 0.7:
                # Packets stream continuously (90 Hz + 1.5 s tail) while
                # flying, so a gap means a new fly session. Re-detect the
                # orbit axis and re-measure the pivot HERE, at fly start -
                # invalidating in the idle branch below re-armed it every
                # time motion paused mid-flight, putting the (expensive)
                # bbox measurement in the middle of the next gesture.
                self.upAxis = None
                _bboxCache['stale'] = True
                _modelCenter()
                dt = 0.016
            else:
                dt = gap
            if dt <= 0.0005:
                return
            dt = min(dt, 0.05)

            mult = pkt.get('sp', 1.0) * (BOOST_MULT if pkt.get('boost') else 1.0)
            tgtVel = [pkt.get('tx', 0.0) * mult,
                      pkt.get('ty', 0.0) * mult,
                      pkt.get('tz', 0.0) * mult]
            tgtOrb = [pkt.get('rx', 0.0), pkt.get('ry', 0.0)]

            aM = 1.0 - math.exp(-dt / TAU_MOVE)
            aO = 1.0 - math.exp(-dt / TAU_ORBIT)
            for i in range(3):
                self.vel[i] += (tgtVel[i] - self.vel[i]) * aM
            for i in range(2):
                self.orbVel[i] += (tgtOrb[i] - self.orbVel[i]) * aO

            # wheel zoom: each notch is a velocity kick that glides out
            wz = pkt.get('wz', 0.0) * (-1.0 if INVERT_WHEEL_ZOOM else 1.0)
            self.wheelVel += wz * WHEEL_ZOOM_KICK
            self.wheelVel *= math.exp(-dt / WHEEL_TAU)

            if (max(abs(v) for v in self.vel) < 1e-4 and
                    max(abs(v) for v in self.orbVel) < 0.05 and
                    abs(self.wheelVel) < 1e-3):
                self.vel = [0.0, 0.0, 0.0]
                self.orbVel = [0.0, 0.0]
                self.wheelVel = 0.0
                # session resets (upAxis, pivot cache) happen on packet-gap
                # detection above, NOT here: this branch also runs when
                # motion merely pauses mid-flight.
                return

            self._applyCamera(dt)
        except Exception:
            _log('nav error:\n' + traceback.format_exc())

    def _applyCamera(self, dt):
        vp = _app.activeViewport
        if not vp:
            return
        cam = vp.camera
        cam.isSmoothTransition = False

        eye = cam.eye.copy()
        tgt = cam.target.copy()
        up = cam.upVector.copy()

        fwd = eye.vectorTo(tgt)
        dist = fwd.length
        if dist < 1e-9:
            return
        fwd.scaleBy(1.0 / dist)

        # ---- spacemouse-style pivot: re-anchor the target to the model by
        # projecting the model center onto the view ray. Zoom then flies
        # toward the actual geometry and orbit rotates around it, instead of
        # around whatever stale point the view center happened to be at. ----
        mc = _modelCenter()
        if mc is not None:
            s = ((mc.x - eye.x) * fwd.x + (mc.y - eye.y) * fwd.y +
                 (mc.z - eye.z) * fwd.z)
            if s > MIN_DIST * 2.0:
                tgt = adsk.core.Point3D.create(eye.x + fwd.x * s,
                                               eye.y + fwd.y * s,
                                               eye.z + fwd.z * s)
                dist = s

        # latch the axis for the whole fly session: re-detecting every frame
        # lets it flip mid-orbit near a pole, which snaps the horizon
        if self.upAxis is None:
            self.upAxis = _modelUpAxis(up, fwd)
        upAxis = self.upAxis

        # ---- orbit (turntable: yaw about world up through target, pitch about
        # camera-right through target, clamped near the poles) ----
        sx = -1.0 if INVERT_ORBIT_X else 1.0
        sy = -1.0 if INVERT_ORBIT_Y else 1.0
        yawA = -self.orbVel[0] * dt * ORBIT_SENS * sx
        pitchA = -self.orbVel[1] * dt * ORBIT_SENS * sy

        if abs(yawA) > 1e-9:
            m = adsk.core.Matrix3D.create()
            m.setToRotation(yawA, upAxis, tgt)
            eye.transformBy(m)
            up.transformBy(m)
            fwd = eye.vectorTo(tgt)
            fwd.scaleBy(1.0 / max(fwd.length, 1e-12))

        # pitch about the *level* right vector, so pitching never adds roll
        right = fwd.crossProduct(upAxis)
        if right.length < 1e-6:
            right = fwd.crossProduct(up)
        if right.length < 1e-9:
            return
        right.normalize()

        if abs(pitchA) > 1e-9:
            m = adsk.core.Matrix3D.create()
            m.setToRotation(pitchA, right, tgt)
            newEye = eye.copy()
            newEye.transformBy(m)
            newFwd = newEye.vectorTo(tgt)
            if newFwd.length > 1e-9:
                newFwd.normalize()
                ang = newFwd.angleTo(upAxis)
                if 0.03 < ang < math.pi - 0.03:   # don't flip over the poles
                    eye = newEye
                    up.transformBy(m)
                    fwd = newFwd

        # ---- constrained orbit: rebuild the frame from the world up axis so
        # roll is always exactly zero. Deriving it from the camera's own up
        # instead carries any existing tilt forever, and pitching about a
        # rolled right-vector adds more of it every frame - which is what made
        # this feel like free orbit. ----
        right = fwd.crossProduct(upAxis)
        if right.length < 1e-6:
            right = fwd.crossProduct(up)    # looking down the axis: keep roll
        right.normalize()
        trueUp = right.crossProduct(fwd)
        trueUp.normalize()

        dist = eye.distanceTo(tgt)
        isOrtho = cam.cameraType == adsk.core.CameraTypes.OrthographicCameraType

        # ---- pan (A/D = truck, Q/E = pedestal; moves eye and target together) ----
        panScale = cam.viewExtents if isOrtho else dist
        px = self.vel[0] * PAN_SPEED * panScale * dt
        py = self.vel[1] * PAN_SPEED * panScale * dt
        if px or py:
            off = adsk.core.Vector3D.create(
                right.x * px + trueUp.x * py,
                right.y * px + trueUp.y * py,
                right.z * px + trueUp.z * py)
            eye.translateBy(off)
            tgt.translateBy(off)

        # ---- dolly / zoom (keys + wheel impulse, exponential toward target) ----
        vz = self.vel[2] * (-1.0 if INVERT_ZOOM else 1.0) + self.wheelVel
        if abs(vz) > 1e-6:
            f = math.exp(-vz * DOLLY_SPEED * dt)
            if isOrtho:
                cam.viewExtents = max(cam.viewExtents * f, 1e-4)
            dist = max(dist * f, MIN_DIST)
            back = fwd.copy()
            back.scaleBy(-dist)
            eye = tgt.copy()
            eye.translateBy(back)

        cam.eye = eye
        cam.target = tgt
        cam.upVector = trueUp
        vp.camera = cam
        vp.refresh()


def _udpLoop():
    global _latest, _pending
    while not _stop.is_set():
        try:
            data, addr = _sock.recvfrom(512)
        except socket.timeout:
            continue
        except OSError:
            break
        try:
            pkt = json.loads(data.decode('utf-8'))
        except Exception:
            continue
        if pkt.get('ping'):
            # heartbeat from the controller UI so it can show "Connected"
            try:
                _sock.sendto(b'{"ack":1}', addr)
            except Exception:
                pass
            continue
        with _lock:
            _latest = pkt
            fire = not _pending
            if fire:
                _pending = True
        if fire:
            try:
                _app.fireCustomEvent(EVENT_ID)
            except Exception:
                pass


def run(context):
    global _app, _ui, _customEvent, _thread, _sock
    try:
        _app = adsk.core.Application.get()
        _ui = _app.userInterface

        try:
            _app.unregisterCustomEvent(EVENT_ID)
        except Exception:
            pass

        _customEvent = _app.registerCustomEvent(EVENT_ID)
        handler = NavEventHandler()
        _customEvent.add(handler)
        _handlers.append(handler)

        _sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        _sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _sock.bind(('127.0.0.1', UDP_PORT))
        _sock.settimeout(0.25)

        _stop.clear()
        _thread = threading.Thread(target=_udpLoop, daemon=True)
        _thread.start()

        _log('running, listening on udp://127.0.0.1:%d' % UDP_PORT)
    except Exception:
        if _ui:
            _ui.messageBox('SpaceMouseWASD failed to start:\n' +
                           traceback.format_exc())


def stop(context):
    global _sock, _thread
    try:
        _stop.set()
        if _sock:
            try:
                _sock.close()
            except Exception:
                pass
            _sock = None
        if _thread:
            _thread.join(timeout=1.0)
            _thread = None
        try:
            _app.unregisterCustomEvent(EVENT_ID)
        except Exception:
            pass
        _handlers.clear()
        _log('stopped')
    except Exception:
        if _ui:
            _ui.messageBox('SpaceMouseWASD failed to stop:\n' +
                           traceback.format_exc())
