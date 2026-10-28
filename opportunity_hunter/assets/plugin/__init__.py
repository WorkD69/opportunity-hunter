"""Native Hermes plugin: loaded only from the Opportunity Hunter profile."""
import json
from pathlib import Path

from .opportunity_hunter.runtime import Runtime

COMMAND_ALIASES=('health','profile','saved','list','save','unsave','hide','unhide',
                 'details','recheck','instructions','confirm','category','frequency')


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
                        'Read the opportunity-hunter skill. No applications or financial actions.'),
        'parameters': {'type': 'object', 'required': ['action'], 'additionalProperties': False,
            'properties': {
                'action': {'type': 'string', 'enum': ['due', 'research', 'store', 'complete_pages', 'page_text', 'list',
                    'details', 'instructions', 'recheck', 'save', 'unsave', 'hide', 'unhide',
                    'preferences', 'profile', 'profile_propose', 'sources', 'source_add', 'health', 'notify']},
                'category': {'type': 'string', 'enum': ['A', 'B', 'C', 'D']},
                'query': {'type': 'string', 'maxLength': 500},
                'url': {'type': 'string'}, 'offset': {'type': 'integer', 'minimum': 0, 'maximum': 60000},
                'id': {'type': 'string'}, 'item': {'type': 'object'}, 'source': {'type': 'object'},
                'pages': {'type': 'array', 'items': {'type': 'object'}},
                'patch': {'type': 'object'}, 'bookmarked': {'type': 'boolean'},
                'days': {'type': 'number', 'minimum': 0, 'maximum': 365},
                'recent_days': {'type': 'number', 'minimum': 0, 'maximum': 365},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 30},
                'enabled': {'type': 'boolean'}, 'interval_hours': {'type': 'number', 'minimum': 6, 'maximum': 720},
                'status': {'type': 'string', 'enum': ['active', 'closed', 'unknown']},
                'kind': {'type': 'string', 'enum': ['new', 'urgent', 'digest']},
                'dry_run': {'type': 'boolean'}
            }}
    }
    ctx.register_tool(name='opportunity_hunter', toolset='opportunity_hunter', schema=schema,
                      handler=handle, description=schema['description'], emoji='🔎')
    ctx.register_command(name='oh', handler=lambda raw: runtime().command(raw),
                         description='Opportunity Hunter: favorites, categories, details, profile confirmations',
                         args_hint='<action> [id]')
    descriptions={'health':'Состояние Opportunity Hunter','profile':'Ваш профиль поиска',
                  'saved':'Избранные предложения','list':'Список предложений',
                  'save':'Добавить предложение в избранное','details':'Условия предложения'}
    for action in COMMAND_ALIASES:
        # Hermes dispatch maps Telegram's oh_health to the internal oh-health name.
        ctx.register_command(name='oh-'+action,
            handler=lambda raw, action=action: runtime().command(action+(' '+str(raw) if raw else '')),
            description=descriptions.get(action,'Opportunity Hunter: '+action),
            args_hint='' if action in ('health','profile','saved') else '[ID / параметры]')
