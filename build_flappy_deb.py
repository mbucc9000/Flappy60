"""Package the 60fps Flappy Bird as a Cydia .deb that installs next to the App Store one.

Source: ipa/Flappy Bird_1.1.0_60fps_SideBySide.ipa (already
decrypted, cryptid 0, with the beq.w -> nop.w patch at VA 0x6844).

The app goes to /Applications/Flappy60fps.app with its own bundle ID and the name
"Flappy 60FPS", so it never replaces com.dotgears.flap. iOS 6 only rounds icons of
App Store apps; /Applications icons are drawn as-is, so the square App Store icons
get rounded corners baked in here. The edited Info.plist and icons are covered by
the code signature (CodeDirectory slot -1 = Info.plist, slot -3 = CodeResources,
which seals each icon), so those hashes are recomputed. Every page hash is checked
to still match, except the pages this script patches (GC_PATCH), which are re-hashed.
postinst runs uicache (as mobile only) so the icon updates without a respring; postrm
only does it on remove.

    python build_flappy_deb.py
"""
import hashlib, io, os, plistlib, re, struct, tarfile, time, zipfile
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
IPA = os.path.join(HERE, 'ipa', 'Flappy Bird_1.1.0_60fps_SideBySide.ipa')
APP_DIR = 'Applications/Flappy60fps.app'
BUNDLE_ID = 'com.mbucc9000.flappybird60fps'
DISPLAY_NAME = 'Flappy 60FPS'
PKG = 'com.mbucc9000.flappybird60fps'
VERSION = '1.1.0-3'

CONTROL = """Package: %s
Name: Flappy 60FPS
Version: %s
Architecture: iphoneos-arm
Description: Flappy Bird 1.1.0 drawing at 60fps instead of 30. The game logic already ran at 60 Hz, but it only drew every other frame. Game speed is unchanged. Also fixes the original game's crash on death when Game Center is signed in. Installs as its own app ("Flappy 60FPS") next to the original.
Author: mbucc9000
Maintainer: mbucc9000
Section: Games
Depends: firmware (>= 6.0), uikittools
""" % (PKG, VERSION)

# Never fall back to a root uicache: on this phone that dropped 41 sideloaded apps.
POSTINST = """#!/bin/sh
# refresh SpringBoard's app list so the icon appears / updates
if [ "$1" = configure ] && [ -x /usr/bin/uicache ]; then
    su -c /usr/bin/uicache mobile 2>/dev/null
fi
exit 0
"""
POSTRM = """#!/bin/sh
# drop the icon on uninstall only (an upgrade's postinst already refreshes it)
if { [ "$1" = remove ] || [ "$1" = purge ]; } && [ -x /usr/bin/uicache ]; then
    su -c /usr/bin/uicache mobile 2>/dev/null
fi
exit 0
"""

ICON_RE = re.compile(r'icon_\d+\.png$')

# Crash on death (bug in the original game): when Game Center counts as signed in,
# -[dot_EAGLView drawView:] reports the score with [[GKScore alloc] initWithCategory:] using a
# leaderboard name that stays nil (the iOS 6 lookup never succeeds now). GameKit raises inside
# the display-link callout, QuartzCore re-throws through an autorelease pool that frees the
# exception, and CoreFoundation's uncaught handler crashes on it. Always take the signed-out
# path (beq -> b), which skips the report and still saves the best score locally.
GC_PATCH = (0x486c, bytes.fromhex('2ad0'), bytes.fromhex('2ae0'))   # beq #0x48c4 -> b #0x48c4

def round_icon(png):
    """Bake iOS 6-style rounded corners (radius = 10/57 of the side) into a square icon."""
    im = Image.open(io.BytesIO(png)).convert('RGBA')
    w, h = im.size
    k = 8                                               # supersample for smooth edges
    mask = Image.new('L', (w * k, h * k), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w * k - 1, h * k - 1], radius=round(w * k * 10 / 57), fill=255)
    mask = mask.resize((w, h), Image.LANCZOS)
    alpha = Image.composite(im.getchannel('A'), Image.new('L', (w, h), 0), mask)
    im.putalpha(alpha)
    out = io.BytesIO()
    im.save(out, 'PNG', optimize=True)
    return out.getvalue()

def code_directory(b):
    ncmds = struct.unpack_from('<I', b, 16)[0]
    off = 28
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<2I', b, off)
        if cmd == 0x21:
            assert struct.unpack_from('<3I', b, off + 8)[2] == 0, 'binary is encrypted'
        if cmd == 0x1d:
            dataoff, _ = struct.unpack_from('<2I', b, off + 8)
        off += size
    _, _, count = struct.unpack_from('>3I', b, dataoff)
    cds = []
    for i in range(count):
        _, o = struct.unpack_from('>2I', b, dataoff + 12 + 8 * i)
        if struct.unpack_from('>I', b, dataoff + o)[0] == 0xfade0c02:
            cds.append(dataoff + o)
    return cds

def vm_to_off(b, addr):
    ncmds = struct.unpack_from('<I', b, 16)[0]
    off = 28
    for _ in range(ncmds):
        cmd, size = struct.unpack_from('<2I', b, off)
        if cmd == 1:   # LC_SEGMENT
            vmaddr, vmsize, fileoff = struct.unpack_from('<3I', b, off + 24)
            if vmaddr <= addr < vmaddr + vmsize:
                return fileoff + addr - vmaddr
        off += size
    raise SystemExit('0x%x is not in any segment' % addr)

def patch(b):
    addr, old, new = GC_PATCH
    o = vm_to_off(b, addr)
    assert bytes(b[o:o + len(old)]) == old, 'unexpected bytes at 0x%x' % addr
    b[o:o + len(new)] = new
    print('  patched 0x%x: skip the Game Center score report at death' % addr)
    return {o}

def resign(b, plist_bytes, code_resources, patched):
    for cd in code_directory(b):
        f = struct.unpack_from('>9I4BI', b, cd)
        hash_off, n_special, n_code, code_limit, hash_size, hash_type, page_log = f[4], f[6], f[7], f[8], f[9], f[10], f[12]
        algo = {1: hashlib.sha1, 2: hashlib.sha256}[hash_type]
        assert n_special >= 3
        for k, blob in ((1, plist_bytes), (3, code_resources)):   # -1 Info.plist, -3 CodeResources
            slot = cd + hash_off - k * hash_size
            b[slot:slot + hash_size] = algo(blob).digest()
        ps = 1 << page_log
        dirty = {o // ps for o in patched}
        for p in range(n_code):                         # untouched pages must still match
            h = algo(bytes(b[p * ps:min((p + 1) * ps, code_limit)])).digest()
            s = cd + hash_off + p * hash_size
            if p in dirty:
                b[s:s + hash_size] = h
            else:
                assert bytes(b[s:s + hash_size]) == h, 'page %d hash mismatch' % p
        print('  CodeDirectory (%s): Info.plist + CodeResources slots updated, %d page(s) re-hashed, %d verified'
              % (algo().name, len(dirty), n_code - len(dirty)))

def ar(members):
    out = bytearray(b'!<arch>\n')
    now = int(time.time())
    for name, body in members:
        out += ('%-16s%-12d%-6d%-6d%-8s%-10d`\n' % (name, now, 0, 0, '100644', len(body))).encode() + body
        if len(body) & 1:
            out += b'\n'
    return bytes(out)

def tar_gz(entries):
    """entries: list of (path, bytes-or-None-for-dir, mode)"""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode='w:gz', format=tarfile.GNU_FORMAT) as t:
        for path, data, mode in entries:
            ti = tarfile.TarInfo(path)
            ti.mode, ti.uid, ti.gid, ti.uname, ti.gname, ti.mtime = mode, 0, 0, 'root', 'wheel', int(time.time())
            if data is None:
                ti.type = tarfile.DIRTYPE
                t.addfile(ti)
            else:
                ti.size = len(data)
                t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()

def main():
    z = zipfile.ZipFile(IPA)
    print('source', os.path.basename(IPA), hashlib.sha256(open(IPA, 'rb').read()).hexdigest()[:16])
    prefix = 'Payload/Flap.app/'

    info = plistlib.loads(z.read(prefix + 'Info.plist'))
    fmt = plistlib.FMT_BINARY if z.read(prefix + 'Info.plist').startswith(b'bplist') else plistlib.FMT_XML
    print('  was: %s / %s' % (info['CFBundleIdentifier'], info['CFBundleDisplayName']))
    info['CFBundleIdentifier'] = BUNDLE_ID
    info['CFBundleDisplayName'] = DISPLAY_NAME
    new_info = plistlib.dumps(info, fmt=fmt)
    print('  now: %s / %s' % (BUNDLE_ID, DISPLAY_NAME))

    icons = {}
    seal = plistlib.loads(z.read(prefix + '_CodeSignature/CodeResources'))
    for name in z.namelist():
        rel = name[len(prefix):]
        if name.startswith(prefix) and ICON_RE.fullmatch(rel):
            icons[rel] = round_icon(z.read(name))
            assert rel in seal['files'], rel
            seal['files'][rel] = hashlib.sha1(icons[rel]).digest()
    new_seal = plistlib.dumps(seal, fmt=plistlib.FMT_XML)
    print('  rounded %d icons, resealed CodeResources' % len(icons))

    exe = info['CFBundleExecutable']
    binary = bytearray(z.read(prefix + exe))
    resign(binary, new_info, new_seal, patch(binary))

    dirs = {'./', './Applications/', './' + APP_DIR + '/'}
    files = []
    for zi in z.infolist():
        if not zi.filename.startswith(prefix) or zi.filename == prefix:
            continue
        rel = zi.filename[len(prefix):]
        dest = './' + APP_DIR + '/' + rel
        if zi.is_dir():
            dirs.add(dest)
            continue
        parent = os.path.dirname(dest) + '/'
        dirs.add(parent)
        if rel == 'Info.plist':
            files.append((dest, new_info, 0o644))
        elif rel == exe:
            files.append((dest, bytes(binary), 0o755))
        elif rel in icons:
            files.append((dest, icons[rel], 0o644))
        elif rel == '_CodeSignature/CodeResources':
            files.append((dest, new_seal, 0o644))
        else:
            files.append((dest, z.read(zi), 0o644))
    data = tar_gz([(d, None, 0o755) for d in sorted(dirs)] + sorted(files))
    control = tar_gz([('./', None, 0o755), ('./control', CONTROL.encode(), 0o644),
                      ('./postinst', POSTINST.encode(), 0o755), ('./postrm', POSTRM.encode(), 0o755)])
    deb = ar([('debian-binary', b'2.0\n'), ('control.tar.gz', control), ('data.tar.gz', data)])
    out = os.path.join(HERE, '..', '..', 'debs', '%s_%s_iphoneos-arm.deb' % (PKG, VERSION))
    open(out, 'wb').write(deb)
    print('wrote %s (%d files, %d bytes) sha256 %s' % (os.path.basename(out), len(files), len(deb), hashlib.sha256(deb).hexdigest()))

if __name__ == '__main__':
    main()
