"""Runtime fixes for py-cord 2.8.0 voice bugs. Call apply() once at startup.

The bug that matters here is VoiceClient._remove_ssrc (discord/voice/client.py:316):

    def _remove_ssrc(self, *, user_id: int) -> None:
        ssrc = self._id_to_ssrc.pop(user_id, None)
        if ssrc:
            self._reader.speaking_timer.drop_ssrc(ssrc)   # <-- unguarded
            self._ssrc_to_id.pop(ssrc, None)

``self._reader`` is ``MISSING`` unless you started recording, and ``MISSING``
raises AttributeError on attribute access. Every other ``_reader`` use in that
file is guarded by ``if self._reader``; this one is not.

Anyone sharing a voice channel with the bot gets an SSRC assigned (opcode 5,
``speaking``), so ``_id_to_ssrc`` is populated. When they leave, Discord sends
``client_disconnect`` (opcode 13), ``VoiceClient._recv_hook`` calls
``_remove_ssrc``, and it raises.

That exception escapes ``VoiceWebSocket.received_message`` -> ``poll_event`` ->
``VoiceConnectionState._poll_ws``, which only catches ``CancelledError``,
``ConnectionClosed`` and ``TimeoutError``. So the runner task dies, and the voice
connection becomes a zombie:

* nothing ever reads the voice socket again, so heartbeat acks stop being
  processed (the keep-alive thread keeps *sending* op 3 into a socket nobody
  reads),
* the DAVE re-key that Discord performs when channel membership changes is never
  applied, so the bot's audio stops being decryptable,
* no reconnect is ever attempted, because the runner that would do it is gone,
* meanwhile ``is_connected()`` still returns True and AudioPlayer keeps
  transmitting, so nothing on the bot's side looks wrong.

Net effect: leave the channel while the bot is playing, come back, and the bot is
sitting there connected and silent until something forces a fresh connection.
"""
from discord.utils import MISSING
from discord.voice import VoiceClient


_APPLIED = False


def _remove_ssrc(self: VoiceClient, *, user_id: int) -> None:
	"""_remove_ssrc with the missing `if self._reader` guard."""
	ssrc = self._id_to_ssrc.pop(user_id, None)
	if not ssrc:
		return

	reader = getattr(self, '_reader', MISSING)
	if reader:
		try:
			reader.speaking_timer.drop_ssrc(ssrc)
		except Exception as error:
			print(f' ERR > drop_ssrc: {error}')

	self._ssrc_to_id.pop(ssrc, None)


def _guarded_recv_hook(original):
	async def recv_hook(self: VoiceClient, ws, msg) -> None:
		try:
			await original(self, ws, msg)
		except Exception as error:
			# Belt and braces for the whole class of bug above: the _poll_ws runner is
			# the only thing that reads the voice socket and reconnects it, so no
			# receive-side handler may ever be allowed to kill it.
			print(f' ERR > voice recv hook (op {msg.get("op")}): {error!r}')

	return recv_hook


def apply() -> None:
	"""Idempotent. Must run before any VoiceClient is constructed.

	create_connection_state() captures `self._recv_hook` as a bound method at
	construction time, so patching the class afterwards would not take effect on
	already-connected clients.
	"""
	global _APPLIED
	if _APPLIED:
		return

	VoiceClient._remove_ssrc = _remove_ssrc
	VoiceClient._recv_hook = _guarded_recv_hook(VoiceClient._recv_hook)
	_APPLIED = True
	print('LOG > Applied py-cord voice patches')
