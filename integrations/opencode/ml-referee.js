import { spawnSync } from 'node:child_process'

const TIMEOUT_MS = 30000

function run(module, payload) {
  return spawnSync('python3', ['-m', `ml_referee.hooks.${module}`, '--harness', 'opencode'], {
    input: JSON.stringify(payload),
    encoding: 'utf8',
    timeout: TIMEOUT_MS,
  })
}

export const MlReferee = async ({ directory }) => ({
  'tool.execute.before': async (input, output) => {
    const result = run('guard', {
      hook_event_name: 'PreToolUse',
      tool_name: input.tool,
      tool_input: output.args,
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
    run('recorder', {
      hook_event_name: 'PostToolUse',
      tool_name: input.tool,
      tool_input: input.args ?? output.args ?? {},
      cwd: directory,
    })
  },
})
