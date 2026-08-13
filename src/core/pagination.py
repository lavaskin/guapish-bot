from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

import discord


PageItemT = TypeVar('PageItemT')


class PaginationView(discord.ui.View, Generic[PageItemT]):
	def __init__(
		self,
		items: Sequence[PageItemT],
		owner_id: int,
		render_page: Callable[[PageItemT, int, int], str | discord.Embed],
		*,
		unauthorized_message: str = 'Only the person who opened this view can change pages.',
		timeout: float = 300,
	):
		super().__init__(timeout=timeout)
		self.items = list(items)
		self.owner_id = owner_id
		self.render_page_content = render_page
		self.unauthorized_message = unauthorized_message
		self.current_page = 0
		self.message = None
		self._sync_buttons()

	def render_current_page(self) -> str | discord.Embed:
		total_pages = len(self.items)
		return self.render_page_content(self.items[self.current_page], self.current_page, total_pages)

	def _page_edit_kwargs(self) -> dict:
		page = self.render_current_page()
		if isinstance(page, discord.Embed):
			return {'content': None, 'embed': page, 'view': self}
		return {'content': page, 'view': self}

	def _sync_buttons(self) -> None:
		previous_button = self.children[0]
		next_button = self.children[1]
		previous_button.disabled = self.current_page == 0
		next_button.disabled = self.current_page >= len(self.items) - 1

	async def _ensure_owner(self, interaction: discord.Interaction) -> bool:
		if interaction.user.id == self.owner_id:
			return True

		await interaction.response.send_message(self.unauthorized_message, ephemeral=True)
		return False

	async def on_timeout(self):
		for child in self.children:
			child.disabled = True

		if self.message is not None:
			await self.message.edit(view=self)

	@discord.ui.button(label='Previous', style=discord.ButtonStyle.secondary)
	async def previous_page(self, button: discord.ui.Button, interaction: discord.Interaction):
		if not await self._ensure_owner(interaction):
			return

		self.current_page -= 1
		self._sync_buttons()
		await interaction.response.edit_message(**self._page_edit_kwargs())

	@discord.ui.button(label='Next', style=discord.ButtonStyle.secondary)
	async def next_page(self, button: discord.ui.Button, interaction: discord.Interaction):
		if not await self._ensure_owner(interaction):
			return

		self.current_page += 1
		self._sync_buttons()
		await interaction.response.edit_message(**self._page_edit_kwargs())
