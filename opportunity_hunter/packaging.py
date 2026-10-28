"""Build a secret-free, parked Hermes profile locally. Never deploys or restarts."""
import json
import shutil
from pathlib import Path

ASSETS = Path(__file__).parent / 'assets'

SOURCES = [
    ('A', 'https://fasie.ru/programs/programma-studstartup/', 'Student Startup', 'official'),
    ('A', 'https://cups.online/', 'All Cups', 'organizer_platform'),
    ('A', 'https://codenrock.com/', 'Codenrock', 'organizer_platform'),
    ('A', 'https://yandex.ru/profi/', 'I am a professional', 'official'),
    ('A', 'https://sberstudent.ru/', 'Sber Student', 'unknown'),
    ('A', 'https://budushchee.alfabank.ru/', 'Alfa Future', 'unknown'),
    ('A', 'https://more.vtb.ru/', 'VTB student opportunities', 'unknown'),
    ('B', 'https://hh.ru/', 'hh.ru', 'aggregator'),
    ('B', 'https://career.habr.com/vacancies', 'Habr Career', 'aggregator'),
    ('B', 'https://career.moex.com/vacancies', 'Moscow Exchange', 'official'),
    ('B', 'https://www.tbank.ru/career/start/', 'T-Start', 'official'),
    ('B', 'https://sbergraduate.ru/', 'Sber Graduate', 'official'),
    ('B', 'https://alfabank.ru/career/', 'Alfa Bank careers', 'official'),
    ('B', 'https://yandex.ru/yaintern/', 'Yandex internships', 'official'),
    ('B', 'https://www.binance.com/en/careers', 'Binance', 'official'),
    ('B', 'https://www.wintermute.com/company/careers', 'Wintermute', 'official'),
    ('B', 'https://www.flowtraders.com/careers/', 'Flow Traders', 'official'),
    ('B', 'https://www.gsr.io/careers/', 'GSR', 'official'),
    ('B', 'https://www.optiver.com/join-us/jobs/', 'Optiver', 'official'),
    ('B', 'https://www.imc.com/eu/careers/', 'IMC', 'official'),
    ('B', 'https://www.janestreet.com/join-jane-street/', 'Jane Street', 'official'),
    ('B', 'https://web3.career/', 'Web3 Career', 'aggregator'),
    ('B', 'https://cryptojobslist.com/', 'CryptoJobsList', 'aggregator'),
    ('B', 'https://www.quantstart.com/quant-jobs/', 'QuantStart jobs', 'aggregator'),
    ('C', 'https://prosperity.imc.com/', 'IMC Prosperity', 'official'),
    ('C', 'https://www.cmegroup.com/events/university-trading-challenge.html', 'CME challenge', 'official'),
    ('C', 'https://platform.worldquantbrain.com/iqc/sign-up', 'WorldQuant IQC', 'official'),
    ('D', 'https://education.tbank.ru/', 'T-Education', 'official'),
    ('D', 'https://siriusuniversity.ru/admission/educational-modules-and-activities/', 'Sirius modules', 'official'),
    ('D', 'https://www.optiver.com/campus-2026/', 'Optiver student programs', 'official'),
    ('D', 'https://www.janestreet.com/join-jane-street/programs-and-events/', 'Jane Street programs', 'official'),
    ('D', 'https://21-school.ru/', 'School 21', 'official'),
]


def cron_spec():
    jobs = []
    for category, minute in zip('ABCD', (5, 20, 35, 50)):
        jobs.append({'name': 'oh-scan-' + category, 'category': category,
                     'schedule': f'{minute} 8,14,20 * * *',
                     'deliver': 'local', 'skills': ['opportunity-hunter'],
                     'enabled_toolsets': ['skills', 'web', 'opportunity_hunter'],
                     'prompt': f'Load opportunity-hunter skill. Category {category} scheduled scan. '
                               'First call opportunity_hunter action=due for this category; if false finish silently. '
                               'Otherwise research with rotating queries plus known sources, verify primary rules, '
                               'store every relevant current/closed/excluded record, complete_pages only after '
                               'successful interpretation. Notify new then urgent via the opportunity tool. '
                               'No direct Telegram send, no applications. If partial, preserve errors in health. '
                               'The final result is stored locally; remain quiet if no useful new results.'})
    jobs.append({'name': 'oh-daily', 'schedule': '0 21 * * *', 'deliver': 'local',
                 'skills': ['opportunity-hunter'], 'enabled_toolsets': ['skills', 'opportunity_hunter'],
                 'prompt': 'Load opportunity-hunter skill. Call notify kind=digest, then kind=urgent. '
                           'Only the opportunity tool delivers. If no new/changed opportunities, stay silent.'})
    return jobs


def build_profile(target):
    target = Path(target).resolve()
    if target.name != 'opportunity-hunter':
        raise ValueError('Target directory must be named opportunity-hunter')
    if target.exists() and any(target.iterdir()):
        raise FileExistsError('Refusing to overwrite an existing profile')
    target.mkdir(parents=True, exist_ok=True)
    for name in ('config.yaml', '.env.example', 'AGENTS.md', 'SOUL.md'):
        shutil.copyfile(ASSETS / name, target / name)
    plugin = target / 'plugins/opportunity-hunter'
    plugin.mkdir(parents=True)
    for name in ('__init__.py', 'plugin.yaml'):
        shutil.copyfile(ASSETS / 'plugin' / name, plugin / name)
    package = plugin / 'opportunity_hunter'
    package.mkdir()
    for path in Path(__file__).parent.glob('*.py'):
        if path.name not in ('packaging.py', 'cli.py'):
            shutil.copyfile(path, package / path.name)
    skill = target / 'skills/opportunity-hunter'
    skill.mkdir(parents=True)
    shutil.copyfile(ASSETS / 'skill/SKILL.md', skill / 'SKILL.md')
    (target / 'sources-seed.json').write_text(json.dumps([
        {'category': c, 'url': u, 'name': n, 'trust': t, 'seed_unverified': True}
        for c, u, n, t in SOURCES], ensure_ascii=False, indent=2), encoding='utf-8')
    (target / 'cron-spec.json').write_text(json.dumps(cron_spec(), ensure_ascii=False, indent=2), encoding='utf-8')
    (target / 'gateway.parked').write_text('Awaiting explicit gateway activation approval and independent credentials.\n', encoding='utf-8')
    shutil.copyfile(ASSETS / 'install_cron.py', target / 'install_cron.py')
    return target
