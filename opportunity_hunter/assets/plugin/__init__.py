"""Native Hermes plugin: loaded only from the Opportunity Hunter profile."""
import json
import logging
from pathlib import Path

from .opportunity_hunter import buttons
from .opportunity_hunter.runtime import Runtime

logger = logging.getLogger(__name__)

COMMAND_ALIASES=('menu','health','profile','saved','list','save','unsave','hide','unhide',
                 'details','recheck','instructions','confirm','cancel','pending','category','frequency',
                 'rejected','recent','categories','help')


def register(ctx):
    from hermes_constants import get_hermes_home
    from agent.secret_scope import get_secret
    from gateway.session_context import get_session_env
    home = Path(get_hermes_home()).resolve()
    if ctx.profile_name != 'opportunity-hunter' or home.name != 'opportunity-hunter':
        raise PermissionError('Plugin is limited to the opportunity-hunter profile')

    def runtime():
        if Path(get_hermes_home()).resolve() != home:
            raise PermissionError('Foreign profile context')
        return Runtime(home, lambda name, args: ctx.dispatch_tool(name, args), get_secret, get_session_env)

    def handle(arguments, **kwargs):
        try:
            return json.dumps(runtime().handle(arguments), ensure_ascii=False)
        except (ValueError, KeyError, PermissionError) as exc:
            return json.dumps({'error': str(exc), 'success': False}, ensure_ascii=False)

    schema = {
        'name': 'opportunity_hunter',
        'description': ('Research and save opportunities using native Hermes web tools and an evidence ledger. '
                        'Use this for scholarships/jobs/free trading tournaments/free career programs. '
                        'Also reads the owner profile (criteria + resume facts) and PROPOSES changes; '
                        'the owner applies them with a button. Read the opportunity-hunter skill. '
                        'No applications or financial actions.'),
        'parameters': {'type': 'object', 'required': ['action'], 'additionalProperties': False,
            'properties': {
                'action': {'type': 'string', 'enum': ['due', 'research', 'store', 'complete_pages', 'page_text', 'list',
                    'details', 'instructions', 'recheck', 'save', 'unsave', 'hide', 'unhide',
                    'preferences', 'profile', 'profile_propose', 'resume_profile', 'resume_propose',
                    'profile_missing', 'rejected', 'sources', 'source_add', 'health', 'notify']},
                'category': {'type': 'string', 'enum': ['A', 'B', 'C', 'D']},
                'query': {'type': 'string', 'maxLength': 500},
                'url': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 60000},
                'id': {'type': 'string'}, 'item': {'type': 'object'}, 'source': {'type': 'object'},
                'pages': {'type': 'array', 'items': {'type': 'object'}},
                'patch': {'type': 'object'}, 'bookmarked': {'type': 'boolean'},
                'ops': {'type': 'array', 'maxItems': 50, 'items': {'type': 'object'}},
                'days': {'type': 'number', 'minimum': 0, 'maximum': 365},
                'recent_days': {'type': 'number', 'minimum': 0, 'maximum': 365},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 30},
                'enabled': {'type': 'boolean'}, 'interval_hours': {'type': 'number', 'minimum': 6, 'maximum': 720},
                'status': {'type': 'string', 'enum': ['active', 'closed', 'unknown']},
                'kind': {'type': 'string', 'enum': ['new', 'urgent', 'digest']},
                'dry_run': {'type': 'boolean'}
            }}
    }
    def profile_scope():
        """Bind this profile's home/secrets while a button press is handled outside a Hermes turn."""
        try:
            from gateway.run import _profile_runtime_scope
        except Exception:
            logger.error('Opportunity Hunter buttons disabled: Hermes profile scope API unavailable')
            return None
        return _profile_runtime_scope(home)

    def press(data, user_id, chat_id, bot_id):
        if not isinstance(data, str) or not data.startswith(buttons.PREFIX):
            return None
        try:
            scope = profile_scope()
            if scope is None:  # fail closed: never read secrets or the store outside this profile's scope
                return None
            scope.__enter__()
        except Exception:
            logger.warning('Opportunity Hunter buttons: profile scope failed', exc_info=True)
            return None
        try:
            try:
                token = str(get_secret('TELEGRAM_BOT_TOKEN') or '')
                owner = str(get_secret('OH_OWNER_ID') or '')
            except Exception:
                logger.warning('Opportunity Hunter buttons: secrets unavailable', exc_info=True)
                return None
            # The plugin manager is process-wide, so this factory is wired to every Telegram adapter
            # (default profile included): act only for our own bot, and only for the owner. Anyone
            # else gets nothing at all — not even an error toast.
            if not token or token.split(':', 1)[0] != str(bot_id):
                return None
            if not owner or str(user_id) != owner or str(chat_id) != owner:
                return None
            try:
                return buttons.dispatch(runtime(), data, user_id, chat_id)
            except Exception:
                logger.warning('Opportunity Hunter button failed', exc_info=True)
                return {'toast': 'Не получилось, попробуй ещё раз', 'alert': False, 'mode': None}
        finally:
            scope.__exit__(None, None, None)

    def telegram_handlers(native, adapter=None):
        import asyncio
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        from telegram.error import BadRequest
        from telegram.ext import CallbackQueryHandler
        try:
            from telegram import LinkPreviewOptions
            no_preview = {'link_preview_options': LinkPreviewOptions(is_disabled=True)}
        except ImportError:  # older python-telegram-bot
            no_preview = {'disable_web_page_preview': True}

        def markup(data):
            if not data:
                return None
            return InlineKeyboardMarkup([[InlineKeyboardButton(b['text'], callback_data=b['callback_data']) for b in row]
                                         for row in data['inline_keyboard']])

        async def on_press(update, context):
            query = update.callback_query
            chat = query.message.chat.id if query.message else None
            try:
                result = await asyncio.to_thread(press, query.data, query.from_user.id, chat, context.bot.id)
            except Exception:  # press() already returns None for anyone but the owner on our bot
                logger.warning('Opportunity Hunter button failed', exc_info=True)
                return
            if result is None:
                return
            # Always answer the owner: otherwise Telegram keeps a spinner on the button. A failed answer
            # (e.g. «query is too old» after a restart) must not stop the screen update below.
            try:
                await query.answer((result.get('toast') or '')[:200] or None, show_alert=bool(result.get('alert')))
            except BadRequest as exc:
                logger.info('Opportunity Hunter: callback answer failed: %s', exc)
            mode, text, keyboard = result.get('mode'), result.get('text'), markup(result.get('markup'))
            try:
                if mode == 'keyboard':
                    await query.edit_message_reply_markup(reply_markup=keyboard)
                elif mode == 'edit' and text:
                    await query.edit_message_text(text[:4000], reply_markup=keyboard, **no_preview)
                elif mode == 'send' and text:
                    await context.bot.send_message(chat_id=chat, text=text[:4000], reply_markup=keyboard, **no_preview)
            except BadRequest as exc:
                if 'not modified' in str(exc).lower():
                    return  # same content (e.g. «Обновить» with nothing new) — nothing to do
                if mode == 'edit' and text and chat is not None:
                    # The pressed message can no longer be edited: show the screen as a new panel instead.
                    await context.bot.send_message(chat_id=chat, text=text[:4000], reply_markup=keyboard, **no_preview)
                else:
                    logger.warning('Opportunity Hunter button update failed: %s', exc)

        # Scoped pattern: core flows (approvals, model picker) keep their own callback prefixes.
        native.add_handler(CallbackQueryHandler(on_press, pattern=r'^oh:'))

    if hasattr(ctx, 'register_telegram_handler'):
        ctx.register_telegram_handler(telegram_handlers)

    ctx.register_tool(name='opportunity_hunter', toolset='opportunity_hunter', schema=schema,
                      handler=handle, description=schema['description'], emoji='🔎')
    ctx.register_command(name='oh', handler=lambda raw: runtime().command(raw),
                         description='Opportunity Hunter: favorites, categories, details, profile confirmations',
                         args_hint='<action> [id]')
    descriptions={'menu':'Меню с кнопками','health':'Состояние бота','profile':'Мой профиль',
                  'saved':'Избранное','list':'Список предложений','rejected':'Отсеянное и почему',
                  'recent':'Новое за 3 дня','categories':'Категории и частота','help':'Что умеет бот',
                  'pending':'Прислать правки профиля, ждущие подтверждения',
                  'save':'Сохранить предложение','details':'Подробнее о предложении'}
    for action in COMMAND_ALIASES:
        # Hermes dispatch maps Telegram's oh_health to the internal oh-health name.
        ctx.register_command(name='oh-'+action,
            handler=lambda raw, action=action: runtime().command(action+(' '+str(raw) if raw else '')),
            description=descriptions.get(action,'Opportunity Hunter: '+action),
            args_hint='' if action in ('menu','health','profile','saved','rejected','recent','categories','help','pending')
                      else '[ID / параметры]')
