"""One-time, non-destructive migration of Mateo's LiveKit avatar from Simli to Anam.
Run from project root: python switch_mateo_to_anam.py
"""
from pathlib import Path
from datetime import datetime
import ast
import re
import shutil

path = Path('agent/agent.py')
if not path.is_file():
    raise SystemExit('Run this script from the livekit-avatar-starter project root.')
source = path.read_text(encoding='utf-8')
if 'from livekit.plugins import anam' in source and 'Starting Anam avatar' in source:
    raise SystemExit('Anam migration already present; no changes made.')
if 'from livekit.plugins import simli' not in source:
    raise SystemExit('Expected Simli import not found. No changes made.')

# Match the two versions shared in chat, preserving everything outside this region.
start_markers = [
    '    # Simli avatar initialization and diagnostics',
    '    # SIMLI AVATAR - EXTENDED IDLE TIME',
]
start = -1
for marker in start_markers:
    start = source.find(marker)
    if start >= 0:
        # Remove the preceding separator only when present.
        if marker.startswith('    # SIMLI AVATAR'):
            preceding = source.rfind('    # =================================================', 0, start)
            if preceding >= 0 and not source[preceding:start].strip(' \r\n#='):
                start = preceding
        break
if start < 0:
    raise SystemExit('Simli block not recognized. No changes made; share your current agent.py.')

end_match = re.search(r'^    initial_participant_id = None\s*$', source[start:], re.MULTILINE)
if not end_match:
    raise SystemExit('Cannot locate the next participant-selection block. No changes made.')
end = start + end_match.start()
old_block = source[start:end]
if 'avatar.start(' not in old_block or 'enable_simli' not in old_block:
    raise SystemExit('Unexpected Simli block structure. No changes made.')

new_block = '''    # =================================================
    # ANAM AVATAR (replaces Simli; voice / RAG remain unchanged)
    # =================================================
    avatar_provider = os.getenv("AVATAR_PROVIDER", "anam").strip().lower()
    if avatar_provider == "anam":
        anam_api_key = os.getenv("ANAM_API_KEY", "").strip()
        anam_avatar_id = os.getenv("ANAM_AVATAR_ID", "").strip()
        anam_avatar_model = os.getenv("ANAM_AVATAR_MODEL", "").strip()

        logger.info(
            "ANAM DEBUG | enabled=True | key=%s | avatar_id=%s",
            bool(anam_api_key),
            bool(anam_avatar_id),
        )
        if not anam_api_key or not anam_avatar_id:
            raise RuntimeError("ANAM_API_KEY or ANAM_AVATAR_ID is missing in .env")

        persona_kwargs = {
            "name": "Mateo",
            "avatarId": anam_avatar_id,
        }
        if anam_avatar_model:
            persona_kwargs["avatarModel"] = anam_avatar_model

        avatar = anam.AvatarSession(
            persona_config=anam.PersonaConfig(**persona_kwargs),
            api_key=anam_api_key,
            avatar_participant_name="Mateo",
        )
        logger.info("Starting Anam avatar")
        try:
            # Must start BEFORE session.start so avatar receives speech audio.
            await avatar.start(session, room=ctx.room)
        except Exception:
            logger.exception("Anam avatar startup failed")
            raise
        logger.info("Anam avatar start completed; verify its video track")
    elif avatar_provider in {"none", "off", "voice"}:
        logger.warning("Avatar disabled: voice-only mode")
    else:
        raise RuntimeError(
            f"Unsupported AVATAR_PROVIDER={avatar_provider!r}. Use 'anam' or 'voice'."
        )


'''
updated = source.replace('from livekit.plugins import simli', 'from livekit.plugins import anam', 1)
# Recalculate spans after import replacement (length changes).
start = updated.find(source[start:start+min(45,len(source)-start)])
if start < 0:
    raise SystemExit('Could not relocate avatar block. No changes made.')
end_match = re.search(r'^    initial_participant_id = None\s*$', updated[start:], re.MULTILINE)
if not end_match:
    raise SystemExit('Could not relocate participant code. No changes made.')
end = start + end_match.start()
updated = updated[:start] + new_block + updated[end:]
try:
    ast.parse(updated, filename=str(path))
except SyntaxError as exc:
    raise SystemExit(f'Generated code failed syntax check: {exc}; no changes made.')
backup = path.with_name(f'agent_before_anam_{datetime.now():%Y%m%d_%H%M%S}.py')
shutil.copy2(path, backup)
path.write_text(updated, encoding='utf-8')
print(f'Updated: {path}')
print(f'Backup:  {backup}')
print('Python syntax check: PASS')
print('Next: restart agent, create a new meeting, check ANAM DEBUG and Starting Anam avatar.')
