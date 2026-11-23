import json
import logging
import os
import re
import sys
import time

import yaml
from flask import Flask, render_template, request, jsonify, Blueprint, abort, Response
from flask import current_app
from flask_wtf import FlaskForm
from flask_wtf.csrf import CSRFProtect
from wtforms.validators import InputRequired, Optional as OptionalV, NumberRange
from wtforms import StringField, SubmitField, IntegerField, FloatField

import flask_apps.shared_state as shared_state
import flask_apps.lanes as lanes_mod
from PythonTools.server_status import isCommandRunable

### Pedestal run, HV optional: the Multi Pedestal Run, at several bias voltages
### per slot when a lane has HV on. Up to 8 Krias side by side, one lane each.
JOBMODE = 'task5'

### the serial -> bring-up parameter table lives in one place, next to the bench
### scripts that use it, so the form and the runner can never disagree.
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'scripts', 'task4_mmts_pedestal'))
import board_params  # noqa: E402

MMTS_CONF = 'data/mmts_configurations.yaml'
andrewCONF = f'{os.environ.get("AndrewModuleTestingGUI_BASE")}/configuration.yaml'
dirDAQresult = ''
DEFAULT_INSPECTORS = []
try:
    with open(andrewCONF, 'r') as fIN:
        conf = yaml.safe_load(fIN)
        DEFAULT_INSPECTORS = conf['Inspectors']
        dirDAQresult = f"{conf['DataLoc']}/daqplots/"
except FileNotFoundError as e:
    raise FileNotFoundError(f'\n\n[NoEnvVar] Need to `source ./init_bash_vars.sh` before execute this file') from e

BENCH = {}
KEITHLEY = {}
_conf = {}
try:
    with open(MMTS_CONF, 'r') as fIN:
        _conf = yaml.safe_load(fIN) or {}
        BENCH = _conf.get('MMTS_bench', {}) or {}
        KEITHLEY = (_conf.get('MMTS_hardwares', {}) or {}).get('keithley', {}) or {}
except FileNotFoundError:
    pass
SLOTS = [str(s) for s in BENCH.get('slots', ['A', 'B', 'C'])]
HV = BENCH.get('hv', {}) or {}
### Vitrek relay per tray position ("3C" -> 8). A lane is a row, its slots L/C/R.
CHANNELS = {}
for _pos, _c in (_conf.get('MMTS_channel_config') or {}).items():
    try:
        CHANNELS[str(_pos)] = int((_c or {}).get('HVchannel'))
    except (TypeError, ValueError):
        pass
SLOT_POS = {'A': 'L', 'B': 'C', 'C': 'R'}
### The lock hv_lock.sh takes for the Keithley; the page shows who holds it.
HV_LOCK = os.environ.get('MMTS_HV_SESSION_LOCK', os.path.join(ROOT, 'logs', 'hv_session.lock'))
### The instrument's ceiling. board_params caps each geometry further (LD 600).
HVMAX = int(KEITHLEY.get('MaxVoltage', 800))
### [(host, name)]: the host reaches the bench, the name is what the page shows
KRIAS = lanes_mod.parse_krias(BENCH)

DEFAULTS = {
        'inspector': '',
        'moduleSTATUS': '',
        'moduleIDA': '', 'moduleIDB': '', 'moduleIDC': '',
        'powerBoardA': 'auto', 'powerBoardB': 'auto', 'powerBoardC': 'auto',
        'nRUNS': str(BENCH.get('default_nruns', 5)),
        'label': str(BENCH.get('default_label', 'ped')),
        'skipPROBE': str(BENCH.get('skip_probe', 1)),
        'bringupTRIES': str(BENCH.get('bringup_tries', 12)),
        'biasVOLTS': '1,10,100,300,500',
        'hvSwitch': str(HV.get('switch', 'none')),
        'useHV': '0',
        'temperature': '', 'humidity': '',
        }
### The leakage abort is the config's, no longer a form field.
MAXLEAK = str(HV.get('leak_abort_uA', 1000))
### Last temperature and humidity typed in, so a page reload keeps them.
LAST_COND = os.path.join(ROOT, 'logs', 'last_conditions.json')


def last_conditions():
    try:
        with open(LAST_COND) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

BENCH_KEYS = ['moduleIDA', 'moduleIDB', 'moduleIDC',
              'powerBoardA', 'powerBoardB', 'powerBoardC',
              'nRUNS', 'label', 'skipPROBE', 'bringupTRIES',
              'biasVOLTS', 'hvSwitch', 'maxLEAK', 'useHV', 'temperature', 'humidity']

logger = logging.getLogger('werkzeug')

app = Blueprint('app_task5', __name__)

LANES = lanes_mod.load_lanes(BENCH, ROOT)
BY_NAME = {l.name: l for l in LANES}


def update_aggregate():
    """The one status the rest of the GUI sees, so the job mode switch on the
    main page still only works with every lane at startup or cleared."""
    st = [l.status for l in LANES]
    if all(s in lanes_mod.FREE for s in st):
        shared_state.server_status = 'destroyed' if 'destroyed' in st else 'startup'
    elif any(s in lanes_mod.BUSY for s in st):
        shared_state.server_status = 'running'
    else:
        shared_state.server_status = 'idle'


for _l in LANES:
    _l.conf = dict(DEFAULTS)
    _l.on_status = update_aggregate


def parse_bias(text, cap=None):
    """'1, 10,300' -> [1, 10, 300]. Raises ValueError on anything else.

    The same check run_slots_hv.sh makes before it powers anything: a typo that
    reaches the Keithley as 5000 is not worth discovering with a module on the
    cable.
    """
    out = []
    for tok in str(text or '').replace(' ', '').split(','):
        if not tok:
            continue
        if not tok.isdigit():
            raise ValueError(f'"{tok}" is not a whole number of volts')
        v = int(tok)
        if cap is not None and v > cap:
            raise ValueError(f'{v} V is above the limit of {cap} V')
        out.append(v)
    if not out:
        raise ValueError('give at least one bias voltage')
    return out


def ExecCMD(lane, jobID):
    ### debug_mode prints what each button would run instead of running it, so
    ### the whole UI can be driven on a laptop with no bench attached.
    make = 'make -n' if shared_state.debug_mode else 'make'
    hv = '1' if lane.use_hv else '0'
    if jobID == 'Init':
        ### Plain preflight: HV is chosen at Configure, and an HV run checks the
        ### Keithley itself before anything is powered.
        return f'{make} -f makefile_task5 initialize useHV=0 JobName=Init'
    if jobID == 'Run':
        lane.runidx += 1
        ### unique across GUI restarts: the tag names the gallery folder and the log file
        lane.runtag = f'{lane.name}_{time.strftime("%Y%m%d_%H%M%S")}'
        opts = ' '.join(f"{k}='{v}'" for k, v in lane.conf.items()
                        if k in BENCH_KEYS and v != '')
        ### never -j: one Kria, one i2c-server, one daq-server, one trigger claim
        return f'{make} -f makefile_task5 run JobName=Run runTAG={lane.runtag} {opts}'
    sw = lane.conf.get('hvSwitch') or 'none'
    if jobID == 'Stop':
        return f'{make} -f makefile_task5 stop useHV={hv} hvSwitch={sw} JobName=Stop'
    if jobID == 'Destroy':
        return f'{make} -f makefile_task5 destroy useHV={hv} hvSwitch={sw} JobName=Destroy'


def check_jobmode() -> bool:
    if not shared_state.jobmode:
        logger.info(f'[ReplaceJobMode] jobmode modified from None to {JOBMODE}')
        shared_state.jobmode = JOBMODE
        return True
    if shared_state.jobmode == JOBMODE:
        return True
    logger.warning(f'[InvalidJobMode] jobmode "{shared_state.jobmode}" mismatched with local "{JOBMODE}". Ignore command')
    return False


def relay_of(lane, slot):
    """(position, Vitrek relay) for a slot of a lane, or (position, None)."""
    pos = f'{lane.hv_row}{SLOT_POS[slot]}'
    return pos, CHANNELS.get(pos)


def keithley_owner():
    """'K2 B' while a live run holds the Keithley, else ''."""
    try:
        with open(os.path.join(HV_LOCK, 'owner')) as f:
            parts = f.read().split()
        os.kill(int(parts[2]), 0)
        return ' '.join(parts[:2])
    except PermissionError:
        return ' '.join(parts[:2])
    except (OSError, IndexError, ValueError):
        return ''


def lane_or_404(name):
    lane = BY_NAME.get(name)
    if lane is None:
        return None, (jsonify({'status': 'error', 'message': f'no lane {name}'}), 404)
    return lane, None


def refuse(msg, code=409):
    return jsonify({'status': 'error', 'message': msg}), code


# --------------------------------------------------------------------------
# Lane actions
# --------------------------------------------------------------------------
@app.route('/lane/<name>/init', methods=['POST'])
def Init(name):
    lane, err = lane_or_404(name)
    if err: return err
    if not check_jobmode(): return '', 204
    if not isCommandRunable(lane.status, 'Init'):
        return refuse(f'{name} is {lane.status}; Clear it first')

    kria = str((request.get_json(silent=True) or {}).get('kria', lane.kria) or '').strip()
    if not lanes_mod.valid_host(kria):
        return refuse(f'{name}: "{kria}" is not a hostname or an IP address', 400)
    ### one puller per Kria: two lanes on one Kria would steal each other's stream
    for other in LANES:
        if other is not lane and other.kria == kria and other.status not in lanes_mod.FREE:
            return refuse(f'{kria} is already driven by {other.name}')
    lane.kria = kria
    lane.use_hv = False
    lane.stop_flag.clear()
    lane.reset_progress()
    lane.set_status('initializing')

    def worker():
        rc = 1
        try:
            rc = lane.run_command(ExecCMD(lane, 'Init'), logger)
        finally:
            shared_state.DAQresult_current_modified = ''
            ### A refused preflight must NOT leave a Run button armed.
            if rc == 0:
                lane.set_status('initialized')
            else:
                lane.log.append('*** preflight refused: this Kria is not ready. '
                                'Fix what it named, then Clear and Initialize again.')
                lane.set_status('error')

    lane.start('Init', worker)
    return '', 204


class ConfigForm(FlaskForm):
    inspector = StringField("inspector", validators=[InputRequired()])
    moduleSTATUS = StringField("moduleSTATUS", validators=[InputRequired()])
    moduleIDA = StringField("moduleIDA", validators=[OptionalV()])
    moduleIDB = StringField("moduleIDB", validators=[OptionalV()])
    moduleIDC = StringField("moduleIDC", validators=[OptionalV()])
    powerBoardA = StringField("powerBoardA", validators=[OptionalV()])
    powerBoardB = StringField("powerBoardB", validators=[OptionalV()])
    powerBoardC = StringField("powerBoardC", validators=[OptionalV()])
    nRUNS = IntegerField("nRUNS", validators=[InputRequired(), NumberRange(min=1, max=20)])
    bringupTRIES = IntegerField("bringupTRIES",
                                validators=[OptionalV(), NumberRange(min=1, max=40)])
    label = StringField("label", validators=[InputRequired()])
    skipPROBE = StringField("skipPROBE", validators=[OptionalV()])
    useHV = StringField("useHV", validators=[OptionalV()])
    biasVOLTS = StringField("biasVOLTS", validators=[OptionalV()])
    hvSwitch = StringField("hvSwitch", validators=[OptionalV()])
    temperature = FloatField("temperature", validators=[OptionalV(), NumberRange(min=-40, max=80)])
    humidity = FloatField("humidity", validators=[OptionalV(), NumberRange(min=0, max=100)])
    submit = SubmitField("Configure")


@app.route('/lane/<name>/submit', methods=['POST'])
def Configure(name):
    lane, err = lane_or_404(name)
    if err: return err
    if not check_jobmode(): return '', 204
    if not isCommandRunable(lane.status, 'Configure'):
        return refuse(f'{name} is {lane.status}; Initialize it first')

    json_data = request.get_json()
    if not json_data:
        return jsonify({'status': 'error', 'message': 'Missing JSON data'}), 400
    form = ConfigForm(data=json_data)
    if not form.validate_on_submit():
        return jsonify({'status': 'error', 'errors': dict(form.errors)}), 400

    use_hv = str(form.useHV.data or '0') in ('1', 'True', 'true', 'on')

    ### The serial has to reach the bench scripts in the 15-character dashless
    ### form: module_ids.json, the $MM scripts and hexmap_robust.py all read
    ### characters 5 and 6 positionally.
    conf = dict(lane.conf)
    resolved = {}
    errors = {}
    for S in SLOTS:
        raw = getattr(form, f'moduleID{S}').data or ''
        if not raw.strip():
            conf[f'moduleID{S}'] = ''
            continue
        override = (getattr(form, f'powerBoard{S}').data or 'auto').strip().lower()
        try:
            serial, row, power = board_params.params(raw, override)
        except ValueError as e:
            errors[f'moduleID{S}'] = [str(e)]
            continue
        conf[f'moduleID{S}'] = serial
        conf[f'powerBoard{S}'] = override
        resolved[S] = (serial, board_params.describe(row, power), row)

    bias = []
    switch = (form.hvSwitch.data or 'none').strip().lower()
    if use_hv:
        if switch not in ('none', 'vitrek'):
            errors['hvSwitch'] = ['must be none or vitrek']
        others = [o for o in LANES
                  if o is not lane and o.use_hv and o.status not in lanes_mod.FREE]
        if switch == 'none':
            ### One Keithley, one cable moved by hand: one lane with HV at a time.
            for o in others:
                errors['useHV'] = [f'{o.name} already has HV on, and the HV cable is moved by hand; '
                                   'with one cable only one Kria can have HV (or use the Vitrek)']
        else:
            ### With the Vitrek several lanes take turns on the Keithley. They
            ### must all use it, and each needs its own row of relays.
            for o in others:
                if (o.conf.get('hvSwitch') or 'none') != 'vitrek':
                    errors['useHV'] = [f'{o.name} has HV on through the cable; finish or Clear it first']
                elif o.hv_row == lane.hv_row:
                    errors['useHV'] = [f'{o.name} uses the same Vitrek row {lane.hv_row}']
            for S in resolved:
                pos, relay = relay_of(lane, S)
                if relay is None:
                    errors[f'moduleID{S}'] = [f'no Vitrek relay for tray position {pos} '
                                              '(MMTS_channel_config)']
        ### The bias list against BOTH ceilings, so a scan run_slots_hv.sh would
        ### refuse is refused before Run is even armed.
        try:
            bias = parse_bias(form.biasVOLTS.data, HVMAX)
            for S, (_serial, _desc, row) in resolved.items():
                cap = min(int(row['maxv']), HVMAX)
                over = [v for v in bias if v > cap]
                if over:
                    errors['biasVOLTS'] = [f'slot {S} ({row["name"]}) takes at most {cap} V, '
                                           f'and the list asks for {over[0]} V']
        except ValueError as e:
            errors['biasVOLTS'] = [str(e)]

    if errors:
        return jsonify({'status': 'error', 'errors': errors}), 400
    if not resolved:
        return jsonify({'status': 'error', 'errors': f'{name}: no slot has a module serial.'}), 400

    conf['inspector'] = str(form.inspector.data)
    conf['moduleSTATUS'] = str(form.moduleSTATUS.data)
    conf['nRUNS'] = str(form.nRUNS.data)
    conf['bringupTRIES'] = str(form.bringupTRIES.data or 12)
    conf['label'] = re.sub(r'[^A-Za-z0-9_-]+', '', str(form.label.data)) or 'ped'
    conf['skipPROBE'] = '1' if str(form.skipPROBE.data) in ('1', 'True', 'true', 'on') else '0'
    conf['useHV'] = '1' if use_hv else '0'
    t, rh = form.temperature.data, form.humidity.data
    conf['temperature'] = '' if t is None else f'{t:g}'
    conf['humidity'] = '' if rh is None else f'{rh:g}'
    try:
        with open(LAST_COND, 'w') as f:
            json.dump({'temperature': conf['temperature'], 'humidity': conf['humidity'],
                       'at': time.strftime('%Y-%m-%d %H:%M')}, f)
    except OSError:
        pass
    if use_hv:
        conf['biasVOLTS'] = ','.join(str(v) for v in bias)
        conf['hvSwitch'] = switch
        conf['maxLEAK'] = MAXLEAK
    lane.conf = conf
    lane.use_hv = use_hv

    ### Show the power decision and the plan before Run: a module with the power
    ### distribution board fitted and the flag wrong is never powered, and a bias
    ### list nobody read is how 500 V reaches a cable on the wrong bench.
    lines = [f'  {S}: {serial}   {desc}' for S, (serial, desc, _row) in sorted(resolved.items())]
    probe = 'skipped' if conf['skipPROBE'] == '1' else 'RUN (costs a bring-up; no other Kria may run meanwhile)'
    if use_hv:
        if switch == 'none':
            routing = 'one HV cable, moved by hand: the job pauses and asks before each slot'
        else:
            relays = ', '.join(f'{S} relay {relay_of(lane, S)[1]} ({relay_of(lane, S)[0]})'
                               for S in sorted(resolved))
            routing = (f'Vitrek 964i: {relays}. The Keithley is shared: each slot waits '
                       'its turn after its gate, the other Krias keep running')
        plan = (f"\n\n  HV ON: bias points {', '.join(str(v) for v in bias)} V"
                f"\n  {conf['nRUNS']} pedestals per bias point, label '{conf['label']}'"
                f"\n  leakage check aborts a slot above {conf['maxLEAK']} uA below 500 V"
                f"\n  HV routing: {routing}")
        warn = '\n  Check the power column, and check which bench the HV cable is on.'
    else:
        plan = (f"\n\n  HV off: the Keithley is not touched"
                f"\n  {conf['nRUNS']} pedestals per slot, label '{conf['label']}'")
        warn = '\n  Check the power column before pressing Run.'
    cond = (f"\n  conditions: {conf['temperature'] or '?'} C, {conf['humidity'] or '?'} %RH"
            + ('' if conf['temperature'] and conf['humidity'] else '   (not entered)'))
    mesg = (f'{name} ({lane.kria}) configured\n\n' + '\n'.join(lines) + plan + cond
            + f"\n  probe {probe}, up to {conf['bringupTRIES']} bring-up tries per slot"
            + '\n\n  Slots run one at a time, in the order shown.' + warn)

    lane.slots.clear()
    for S in sorted(resolved):
        lane.slot(S)['params'] = resolved[S][1]

    current_app.logger.info(f'[Configure][{name}] {conf}')
    lane.set_status('configured')
    return jsonify({'status': 'success', 'message': mesg}), 200


@app.route('/lane/<name>/run', methods=['POST'])
def Run(name):
    lane, err = lane_or_404(name)
    if err: return err
    if not check_jobmode(): return '', 204
    if not isCommandRunable(lane.status, 'Run'):
        return refuse(f'{name} is {lane.status}; Configure it first')

    ### The 12+12 probe writes the measured map into the shared
    ### <FAMILY>_mux<S>_ped.yaml that every lane reads, so it runs alone.
    running = [l for l in LANES if l is not lane and l.status == 'running']
    if lane.conf.get('skipPROBE') == '0' and running:
        return refuse(f'{name} asks for the 12+12 probe, which runs alone; '
                      f'{", ".join(l.name for l in running)} running')
    probing = [l.name for l in running if l.conf.get('skipPROBE') == '0']
    if probing:
        return refuse(f'{", ".join(probing)} is running the 12+12 probe; wait for it')

    lane.stop_flag.clear()
    lane.log.clear()
    lane.clear_pause()      ### a pause file left by a killed run would release this one
    for S in list(lane.slots):
        lane.set_slot(S, 'queued', 'queued')
    lane.set_status('running')
    cmd = ExecCMD(lane, 'Run')
    ### the run's record next to its log: who, what, when, and the room conditions
    try:
        os.makedirs(lane.pause_dir, exist_ok=True)
        with open(os.path.join(lane.pause_dir, lane.runtag + '.json'), 'w') as f:
            json.dump({'runtag': lane.runtag, 'lane': lane.name, 'kria': lane.kria,
                       'start': time.strftime('%Y-%m-%dT%H:%M:%S'), 'use_hv': lane.use_hv,
                       'conf': lane.conf}, f, indent=1)
    except OSError as e:
        lane.log.append(f'*** could not write the run record: {e}')

    def worker():
        try:
            lane.run_command(cmd, logger)
        finally:
            if lane.status == 'running':
                lane.set_status('idle')

    lane.start('Run', worker)
    return '', 204


@app.route('/lane/<name>/continue', methods=['POST'])
def Continue(name):
    '''Release the "move the HV cable" pause. Safe when nothing is waiting.'''
    lane, err = lane_or_404(name)
    if err: return err
    if not check_jobmode(): return '', 204
    if not lane.read_pause():
        return refuse('nothing is waiting', 400)
    os.makedirs(lane.pause_dir, exist_ok=True)
    open(lane.continue_file, 'w').close()
    current_app.logger.info(f'[Continue][{name}] HV cable pause released')
    return jsonify({'status': 'success'}), 200


@app.route('/lane/<name>/stop', methods=['POST'])
def Stop(name):
    lane, err = lane_or_404(name)
    if err: return err
    if not check_jobmode(): return '', 204

    lane.set_status('stopping')
    ### this lane's local make only; makefile_task5's stop target reaches this
    ### lane's client session, its Kria and, with HV on, the Keithley.
    lane.stop_flag.set()
    lane.kill_group()
    lane.join('Run')
    lane.stop_flag.clear()
    lane.run_command(ExecCMD(lane, 'Stop'), logger)

    for S, d in list(lane.slots.items()):
        if d['state'] not in ('done', 'failed', 'queued'):
            lane.set_slot(S, 'failed', 'stopped by operator')
    lane.set_status('stopped')
    return '', 204


@app.route('/lane/<name>/clear', methods=['POST'])
def Clear(name):
    '''Stop, payload power off, HV off, this lane's puller gone: back to the start.'''
    lane, err = lane_or_404(name)
    if err: return err
    if not check_jobmode(): return '', 204
    if not isCommandRunable(lane.status, 'Destroy'):
        return refuse(f'{name} is {lane.status}; nothing to clear')

    lane.set_status('destroying')
    lane.stop_flag.set()
    lane.kill_group()
    for kind in ('Init', 'Run'):
        lane.join(kind)
    lane.stop_flag.clear()
    lane.run_command(ExecCMD(lane, 'Destroy'), logger)
    lane.use_hv = False
    lane.set_status('destroyed')
    return '', 204


# --------------------------------------------------------------------------
# Page
# --------------------------------------------------------------------------
def result_dirs():
    if not os.path.isdir(dirDAQresult):
        return []
    return sorted(d for d in os.listdir(dirDAQresult)
                  if os.path.isdir(os.path.join(dirDAQresult, d)))


@app.route('/status')
def status():
    ### Always the whole list: a one-shot "changed" flag went to whichever page
    ### (or curl) asked first, and every other page never saw the new run.
    return jsonify({'status': shared_state.server_status,
                    'jobmode': shared_state.jobmode,
                    'DAQres': result_dirs(),
                    'keithley': keithley_owner(),
                    'lanes': [l.snapshot() for l in LANES]})


@app.route('/board_table')
def board_table():
    '''The serial -> parameters table, so the form can label a serial as it is typed.'''
    return jsonify(board_params.TABLE)


@app.route('/main.html')
def main():
    defaults = dict(DEFAULTS)
    last = last_conditions()
    defaults['temperature'] = last.get('temperature', '')
    defaults['humidity'] = last.get('humidity', '')
    return render_template('index_task5.html', DAQres=result_dirs(),
                           inspectors=DEFAULT_INSPECTORS, slots=SLOTS,
                           lanes=LANES, krias=KRIAS, hvmax=HVMAX, defaults=defaults,
                           cond_at=last.get('at', ''), maxleak=MAXLEAK, channels=CHANNELS)


# --------------------------------------------------------------------------
# Full log files: the page pane holds only the tail
# --------------------------------------------------------------------------
_LOGNAME = re.compile(r'^K\d_[A-Za-z0-9_]+\.log$')


def lane_logs(lane):
    try:
        names = [n for n in os.listdir(lane.pause_dir) if _LOGNAME.match(n)]
    except OSError:
        return []
    return sorted(names, key=lambda n: os.path.getmtime(os.path.join(lane.pause_dir, n)), reverse=True)


@app.route('/lane/<name>/log')
def log_page(name):
    lane, err = lane_or_404(name)
    if err: return err
    files = lane_logs(lane)
    pick = request.args.get('f') or (os.path.basename(lane.logfile) if lane.logfile else '') \
        or (files[0] if files else '')
    if pick and pick not in files:
        abort(404)
    return render_template('log_task5.html', lane=lane, files=files, pick=pick,
                           path=os.path.join(lane.pause_dir, pick) if pick else '')


@app.route('/lane/<name>/log.txt')
def log_text(name):
    lane, err = lane_or_404(name)
    if err: return err
    pick = request.args.get('f', '')
    if pick not in lane_logs(lane):
        abort(404)
    with open(os.path.join(lane.pause_dir, pick), errors='replace') as f:
        body = f.read()
    return Response(body, mimetype='text/plain; charset=utf-8',
                    headers={'X-Lane-Busy': '1' if lane.busy() else '0'})


if __name__ == '__main__':
    logging.basicConfig(level=logging.DEBUG,
                        format='[basicCONFIG] %(levelname)s - %(message)s',
                        datefmt='%H:%M:%S')
    app_main = Flask(__name__)
    app_main.register_blueprint(app, url_prefix='/task5')
    app_main.config["SECRET_KEY"] = '7eCZ^6nUxb6hjN5EbLYak&fvt'
    csrf = CSRFProtect(app_main)
    app_main.run(debug=True)
