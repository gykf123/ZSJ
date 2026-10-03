#!/usr/bin/env python3
"""
patch_zsj_mobileglues.py

Fixes required to run Minecraft 26.x on A10 (iPad 6th gen) via MobileGlues 2.0.0.

Root cause of the "invalid version directive" crash:
  MC 26.x delivers GLSL shader sources that BEGIN WITH A NEWLINE, e.g.
      "\\n#version 300 es\\n ...".
  MobileGlues detects the ESSL version with a position-sensitive
  strncmp(glsl, "#version 300 es", 15) (MobileGlues-cpp/gl/shader.cpp:32). A
  leading newline makes that match fail, so is_direct_shader() returns false and
  the shader is forced down the desktop->ESSL conversion path, whose output still
  keeps the leading newline before '#version'. The downstream glslang then sees
  a first line that is not '#version' and rejects it ("invalid version directive"
  -> falls back to ES 1.00 -> 'layout'/'out' all fail -> "Failed to load required
  shader programs" -> CompletionException).

  kiokori-git/Amethyst-iOS does NOT hit this because its tested MC build ships
  shaders whose '#version' is already on the first line. The fix is to normalise
  the shader source by stripping leading whitespace so '#version' lands on the
  first line, before any version detection happens.

  NOTE (build-35+): we now ALSO (a) pass count=1 to the backend
  glShaderSource because s[] only ever holds one merged string (the original
  count caused out-of-bounds reads when MC submitted the shader in multiple
  string segments), and (b) emit a [ZSJ-DIAG] line so we can confirm at runtime
  whether MC's shader compilation actually flows through this glShaderSource at
  all (it may not — MC could be talking to ANGLE directly, bypassing the trim).

This script patches, idempotently, the MobileGlues + glslang sources that are
pulled (as archives, NOT submodules) by the CI's `pull` helper:

  1. (CRITICAL) MobileGlues-cpp/gl/shader.cpp
       - Trim leading whitespace in glShaderSource() before version detection.
       - Trim again (backend-bound) before GLES.glShaderSource().
       - Pass count=1 (single merged string) to the backend.
       - Emit [ZSJ-DIAG] dump of count / head byte / first 32 bytes.

  2. (crash guard) glslang SPIRV/GlslangToSpv.cpp  [kiokori patch_glslang.py]
       Null-check convertSwizzle() so a non-constant swizzle index element
       logs once instead of SIGSEGV-ing the whole JVM (hit by some 26.x
       shaders / optimizer-folded swizzles).

  3. (best-effort) MobileGL MG_Backend multisample probe guard
       [kiokori patch_mobilegl.py] Only applied if the file is present in this
       checkout layout; skipped silently otherwise (newer MobileGlues 2.0.0
       does not ship this path).

Exit code is non-zero ONLY if the critical shader.cpp patch cannot be applied,
so CI fails fast and loudly instead of shipping a broken IPA.

Usage:
    python3 patch_zsj_mobileglues.py /path/to/Natives/external/MobileGlues
"""
import sys
import pathlib

# ---------------------------------------------------------------------------
# Patch 1 (CRITICAL): shader.cpp
# ---------------------------------------------------------------------------
SHADER_CPP_REL = "MobileGlues-cpp/gl/shader.cpp"

# -- 1a. make <cstdio> available so our fprintf diagnostics always compile
SHADER_CSTDIO_ANCHOR = "#include <cctype>\n"
SHADER_CSTDIO_INJECT = (
    "#include <cctype>\n"
    "#include <cstdio>  // [ZSJ-patch-cstdio] for ZSJ diagnostic fprintf\n"
)

# -- 1b. entry trim (before version detection)
SHADER_ANCHOR = (
    "    bool is_sampler_buffer_emulated = hardware->emulate_texture_buffer "
    "&& check_if_sampler_buffer_used(glsl_src);"
)
SHADER_INJECT = (
    "\n"
    "    // [ZSJ-patch-entry-trim] Trim leading whitespace/newlines so '#version'\n"
    "    // lands on the first line. MobileGlues detects the ESSL version with a\n"
    "    // position-sensitive strncmp and glslang requires '#version' at the\n"
    "    // very start; MC 26.x ships shader sources that begin with a newline,\n"
    "    // which otherwise forces a broken desktop->ESSL conversion\n"
    "    // ('invalid version directive' -> ES 1.00 fallback -> shader compile\n"
    "    // failure). Stripping only leading whitespace keeps every valid shader\n"
    "    // version flowing through the correct (direct or converted) path.\n"
    "    {\n"
    "        size_t _zws = 0;\n"
    "        while (_zws < glsl_src.size() && std::isspace((unsigned char)glsl_src[_zws])) _zws++;\n"
    "        if (_zws > 0) glsl_src = glsl_src.substr(_zws);\n"
    "    }\n"
)

# -- 1c. exit trim (backend-bound, after direct/converted selection)
SHADER_ANCHOR2 = "    if (!essl_src.empty()) {"
SHADER_INJECT2 = (
    "    // [ZSJ-patch-exit-trim] Guarantee the source passed to the backend\n"
    "    // begins with '#version'. Trimming here (after direct/converted\n"
    "    // selection) is the final safety net against a leading newline.\n"
    "    {\n"
    "        size_t _zes = 0;\n"
    "        while (_zes < essl_src.size() && std::isspace((unsigned char)essl_src[_zes])) _zes++;\n"
    "        if (_zes > 0) essl_src = essl_src.substr(_zes);\n"
    "    }\n"
)

# -- 1d. diagnostic dump + count=1 fix, right before GLES.glShaderSource
SHADER_SRC_ANCHOR = "        GLES.glShaderSource(shader, count, s, nullptr);"
SHADER_SRC_INJECT = (
    "        // [ZSJ-DIAG] Dump received shader source to confirm the trim + path.\n"
    "        // If this line NEVER appears in the log, MC is compiling shaders\n"
    "        // without going through MobileGlues' glShaderSource (e.g. talking\n"
    "        // to ANGLE directly) and the trims below are inert.\n"
    "        {\n"
    "            static int _zsj_dbg = 0;\n"
    "            bool _abn = essl_src.empty() || (unsigned char)essl_src[0] != '#' ||\n"
    "                        (!glsl_src.empty() && (unsigned char)glsl_src[0] != '#');\n"
    "            if (_zsj_dbg < 6 || _abn) {\n"
    "                _zsj_dbg++;\n"
    "                fprintf(stderr, \"[ZSJ-DIAG] count=%d glsl_head_is_hash=%d essl_head_is_hash=%d glsl_first32:\",\n"
    "                        (int)count,\n"
    "                        (!glsl_src.empty() && (unsigned char)glsl_src[0]=='#') ? 1 : 0,\n"
    "                        (!essl_src.empty() && (unsigned char)essl_src[0]=='#') ? 1 : 0);\n"
    "                for (size_t _b = 0; _b < glsl_src.size() && _b < 32; ++_b)\n"
    "                    fprintf(stderr, \" %02x\", (unsigned char)glsl_src[_b]);\n"
    "                fprintf(stderr, \" | essl_first32:\");\n"
    "                for (size_t _b = 0; _b < essl_src.size() && _b < 32; ++_b)\n"
    "                    fprintf(stderr, \" %02x\", (unsigned char)essl_src[_b]);\n"
    "                fprintf(stderr, \"\\n\");\n"
    "                fflush(stderr);\n"
    "            }\n"
    "        }\n"
    "        // [ZSJ-patch-count1] s[] holds a single merged string; passing the\n"
    "        // original (multi-segment) count reads past s[] bounds.\n"
    "        GLES.glShaderSource(shader, 1, s, nullptr);\n"
)


def patch_shader(root: pathlib.Path) -> int:
    target = root / SHADER_CPP_REL
    if not target.is_file():
        print(f"[ZSJ patch] CRITICAL: {target} not found!", file=sys.stderr)
        return 1
    text = target.read_text()
    changed = False

    # 1a. cstdio include
    if "ZSJ-patch-cstdio" not in text:
        if SHADER_CSTDIO_ANCHOR not in text:
            print("[ZSJ patch] CRITICAL: cctype anchor not found; "
                  "MobileGlues source changed upstream.", file=sys.stderr)
            return 1
        text = text.replace(SHADER_CSTDIO_ANCHOR, SHADER_CSTDIO_INJECT, 1)
        changed = True

    # 1b. entry trim
    if "ZSJ-patch-entry-trim" not in text:
        if SHADER_ANCHOR not in text:
            print(f"[ZSJ patch] CRITICAL: shader.cpp entry anchor not found; "
                  "MobileGlues source changed upstream.", file=sys.stderr)
            return 1
        text = text.replace(SHADER_ANCHOR, SHADER_INJECT + SHADER_ANCHOR, 1)
        changed = True

    # 1c. exit trim
    if "ZSJ-patch-exit-trim" not in text:
        if SHADER_ANCHOR2 not in text:
            print(f"[ZSJ patch] CRITICAL: shader.cpp exit anchor not found; "
                  "MobileGlues source changed upstream.", file=sys.stderr)
            return 1
        text = text.replace(SHADER_ANCHOR2, SHADER_INJECT2 + SHADER_ANCHOR2, 1)
        changed = True

    # 1d. diagnostic + count=1
    if "ZSJ-patch-count1" not in text:
        if SHADER_SRC_ANCHOR not in text:
            print(f"[ZSJ patch] CRITICAL: shader.cpp glShaderSource anchor not found; "
                  "MobileGlues source changed upstream.", file=sys.stderr)
            return 1
        text = text.replace(SHADER_SRC_ANCHOR, SHADER_SRC_INJECT, 1)
        changed = True

    if changed:
        target.write_text(text)
        print(f"[ZSJ patch] Applied/updated shader.cpp patches "
              f"(entry+exit trim, count=1, [ZSJ-DIAG]).")
    else:
        print("[ZSJ patch] shader.cpp already fully patched, skipping.")
    return 0


# ---------------------------------------------------------------------------
# Patch 2 (crash guard): glslang convertSwizzle null-check  [kiokori]
# ---------------------------------------------------------------------------
GLSLANG_OLD = """// Convert a glslang AST swizzle node to a swizzle vector for building SPIR-V.
void TGlslangToSpvTraverser::convertSwizzle(const glslang::TIntermAggregate& node, std::vector<unsigned>& swizzle)
{
    const glslang::TIntermSequence& swizzleSequence = node.getSequence();
    for (int i = 0; i < (int)swizzleSequence.size(); ++i)
        swizzle.push_back(swizzleSequence[i]->getAsConstantUnion()->getConstArray()[0].getIConst());
}"""

GLSLANG_NEW = """// Convert a glslang AST swizzle node to a swizzle vector for building SPIR-V.
void TGlslangToSpvTraverser::convertSwizzle(const glslang::TIntermAggregate& node, std::vector<unsigned>& swizzle)
{
    const glslang::TIntermSequence& swizzleSequence = node.getSequence();
    for (int i = 0; i < (int)swizzleSequence.size(); ++i) {
        const glslang::TIntermConstantUnion* constUnion = swizzleSequence[i]->getAsConstantUnion();
        if (constUnion == nullptr) {
            fprintf(stderr, "[glslang][ZSJ] convertSwizzle: non-constant swizzle index element %d, "
                             "falling back to component 0 instead of crashing\\n", i);
            swizzle.push_back(0);
            continue;
        }
        swizzle.push_back(constUnion->getConstArray()[0].getIConst());
    }
}"""


def patch_glslang(root: pathlib.Path) -> int:
    # glslang is pulled as a nested archive; try both the submodule-style path
    # and MobileGlues' bundled copy so the patch lands on whatever is compiled.
    candidates = [
        root / "MobileGlues-cpp/3rdparty/glslang/SPIRV/GlslangToSpv.cpp",
        root / "MobileGlues-cpp/include/SPIRV/GlslangToSpv.cpp",
    ]
    for target in candidates:
        if not target.is_file():
            continue
        text = target.read_text()
        if "ZSJ" in text and "non-constant swizzle" in text:
            print(f"[ZSJ patch] glslang already patched ({target.name}), skipping.")
            return 0
        if GLSLANG_OLD not in text:
            print(f"[ZSJ patch] glslang convertSwizzle pattern not found in {target}; "
                  "skipping (upstream may have changed).")
            continue
        text = text.replace(GLSLANG_OLD, GLSLANG_NEW, 1)
        target.write_text(text)
        print(f"[ZSJ patch] Applied glslang convertSwizzle null-check guard ({target}).")
        return 0
    print("[ZSJ patch] glslang GlslangToSpv.cpp not found at expected paths; skipping.")
    return 0


# ---------------------------------------------------------------------------
# Patch 3 (best-effort): MobileGL multisample probe guard  [kiokori]
# ---------------------------------------------------------------------------
MOBILEGL_OLD = """            const Bool isMultisample = IsGLESProbeMultisampleTarget(target);
            if (isMultisample) {
                if (target == TextureTarget::Texture2DMultisample && gl.glTexStorage2DMultisample) {
                    gl.glTexStorage2DMultisample(glTarget, 1, internalFormat, 1, 1, GL_TRUE);
                } else if (target == TextureTarget::Texture2DMultisampleArray && gl.glTexStorage3DMultisample) {
                    gl.glTexStorage3DMultisample(glTarget, 1, internalFormat, 1, 1, 1, GL_TRUE);
                } else {
                    gl.glBindTexture(glTarget, static_cast<GLuint>(previousBinding));
                    gl.glDeleteTextures(1, &texture);
                    return false;
                }
            } else {"""

MOBILEGL_NEW = """            const Bool isMultisample = IsGLESProbeMultisampleTarget(target);
            if (isMultisample) {
                const Bool have31 = capabilities.GLESVersion.Major > 3 ||
                    (capabilities.GLESVersion.Major == 3 && capabilities.GLESVersion.Minor >= 1);
                const Bool have32 = capabilities.GLESVersion.Major > 3 ||
                    (capabilities.GLESVersion.Major == 3 && capabilities.GLESVersion.Minor >= 2);
                if (target == TextureTarget::Texture2DMultisample && gl.glTexStorage2DMultisample && have31) {
                    gl.glTexStorage2DMultisample(glTarget, 1, internalFormat, 1, 1, GL_TRUE);
                } else if (target == TextureTarget::Texture2DMultisampleArray && gl.glTexStorage3DMultisample && have32) {
                    gl.glTexStorage3DMultisample(glTarget, 1, internalFormat, 1, 1, 1, GL_TRUE);
                } else {
                    gl.glBindTexture(glTarget, static_cast<GLuint>(previousBinding));
                    gl.glDeleteTextures(1, &texture);
                    return false;
                }
            } else {"""


def patch_mobilegl(root: pathlib.Path) -> int:
    candidates = [
        root / "MobileGL/MG_Backend/DirectGLES/BackendObject_DirectGLES.cpp",
        root / "MobileGlues-cpp/MG_Backend/DirectGLES/BackendObject_DirectGLES.cpp",
    ]
    for target in candidates:
        if not target.is_file():
            continue
        text = target.read_text()
        if "have31" in text and "have32" in text:
            print(f"[ZSJ patch] mobilegl multisample guard already applied ({target.name}).")
            return 0
        if MOBILEGL_OLD not in text:
            print(f"[ZSJ patch] mobilegl multisample pattern not found in {target}; skipping.")
            continue
        text = text.replace(MOBILEGL_OLD, MOBILEGL_NEW, 1)
        target.write_text(text)
        print(f"[ZSJ patch] Applied mobilegl multisample probe guard ({target}).")
        return 0
    print("[ZSJ patch] mobilegl BackendObject_DirectGLES.cpp not present in this layout; skipping (best-effort).")
    return 0


def main() -> int:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} /path/to/Natives/external/MobileGlues", file=sys.stderr)
        return 2
    root = pathlib.Path(sys.argv[1])
    if not root.is_dir():
        print(f"[ZSJ patch] MobileGlues root {root} is not a directory.", file=sys.stderr)
        return 2

    rc = patch_shader(root)        # critical -> propagates failure
    patch_glslang(root)            # best-effort
    patch_mobilegl(root)           # best-effort
    return rc


if __name__ == "__main__":
    sys.exit(main())
