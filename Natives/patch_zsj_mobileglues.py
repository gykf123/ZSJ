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

This script patches, idempotently, the MobileGlues + glslang sources that are
pulled (as archives, NOT submodules) by the CI's `pull` helper:

  1. (CRITICAL) MobileGlues-cpp/gl/shader.cpp
       Trim leading whitespace in glShaderSource() before version detection.

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
# Patch 1 (CRITICAL): trim leading whitespace in glShaderSource()
# ---------------------------------------------------------------------------
SHADER_CPP_REL = "MobileGlues-cpp/gl/shader.cpp"

SHADER_ANCHOR = (
    "    bool is_sampler_buffer_emulated = hardware->emulate_texture_buffer "
    "&& check_if_sampler_buffer_used(glsl_src);"
)

SHADER_INJECT = (
    "\n"
    "    // [ZSJ patch] Trim leading whitespace/newlines so '#version' lands on\n"
    "    // the first line. MobileGlues detects the ESSL version with a\n"
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


def patch_shader(root: pathlib.Path) -> int:
    target = root / SHADER_CPP_REL
    if not target.is_file():
        print(f"[ZSJ patch] CRITICAL: {target} not found!", file=sys.stderr)
        return 1
    text = target.read_text()
    if "ZSJ patch" in text:
        print("[ZSJ patch] shader.cpp already patched, skipping.")
        return 0
    if SHADER_ANCHOR not in text:
        print("[ZSJ patch] CRITICAL: shader.cpp anchor not found; "
              "MobileGlues source changed upstream.", file=sys.stderr)
        return 1
    text = text.replace(SHADER_ANCHOR, SHADER_INJECT + SHADER_ANCHOR, 1)
    target.write_text(text)
    print(f"[ZSJ patch] Applied critical shader.cpp leading-whitespace trim.")
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
