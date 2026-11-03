import { spawnSync } from 'node:child_process'

const TIMEOUT_MS = 30000
// The installer replaces these two constants; unrendered, the plugin falls back to python3.
const PYTHON = null // ml-referee:python
const PYTHONPATH_DIR = null // ml-referee:pythonpath

function run(module, payload) {
  const env = { ...process.env }
  if (PYTHONPATH_DIR) {
    env.PYTHONPATH = env.PYTHONPATH ? `${PYTHONPATH_DIR}:${env.PYTHONPATH}` : PYTHONPATH_DIR
  }
  return spawnSync(PYTHON ?? 'python3', ['-m', `ml_referee.hooks.${module}`, '--harness', 'opencode'], {
    input: JSON.stringify(payload),
    encoding: 'utf8',
    timeout: TIMEOUT_MS,
    env,
  })
}

export const MlReferee = async ({ directory }) => ({
  'tool.execute.before': async (input, output) => {
    const result = run('guard', {
      hook_event_name: 'PreToolUse',
      tool_name: input.tool,
      tool_input: output?.args ?? input?.args ?? {},
      cwd: directory,
    })
    // Fail closed: anything other than a clean exit 0 blocks the tool call.
    if (result.error || result.signal || result.status === null) {
      const why = result.error ? result.error.message : `guard ended abnormally (${result.signal || 'no status'})`
      throw new Error(`ml-referee guard failed: ${why}`)
    }
    if (result.status !== 0) {
      const stderr = String(result.stderr || '').trim()
      throw new Error(stderr || `ml-referee guard exited with status ${result.status}`)
    }
  },
  'tool.execute.after': async (input, output) => {
    // Recording is best-effort; the guard is the enforcement point.
    const exitCode = output?.metadata?.exitCode ?? output?.exitCode
    const payload = {
      hook_event_name: 'PostToolUse',
      tool_name: input.tool,
      tool_input: input.args ?? output?.args ?? {},
      cwd: directory,
    }
    if (Number.isInteger(exitCode)) payload.exit_code = exitCode
    run('recorder', payload)
  },
})
