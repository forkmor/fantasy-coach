import { useEffect, useState } from 'react'
import { request } from './api'
import Modal from './Modal'
import type { AppState } from './types'
import { setupIssues, validTimezone } from './validation'
import { Badge, dateTime, Notice, OperationStatus, SectionHeading, useOperation } from './ui'

export default function ScheduleEditor({ state, onSaved }: { state: AppState; onSaved: () => Promise<void> }) {
  const [enabled, setEnabled] = useState(state.schedule.enabled)
  const [timezone, setTimezone] = useState(state.schedule.timezone)
  const [interval, setInterval] = useState(String(state.schedule.interval_minutes))
  const [review, setReview] = useState(false)
  const op = useOperation()
  useEffect(() => {
    setEnabled(state.schedule.enabled)
    setTimezone(state.schedule.timezone)
    setInterval(String(state.schedule.interval_minutes))
  }, [state.schedule.enabled, state.schedule.timezone, state.schedule.interval_minutes])
  const issues = setupIssues(state)
  const choices = [{ minutes: 1440, title: 'Once a day', help: 'A sensible starting point' },
    { minutes: 720, title: 'Twice a day', help: 'A little more attention' },
    { minutes: 60, title: 'Every hour', help: 'More checks and API usage' }]
  const frequency = choices.find(choice => choice.minutes === Number(interval))?.title ?? `Every ${interval} minutes`
  function validate() {
    const minutes = Number(interval)
    if (!Number.isInteger(minutes) || minutes < 5 || minutes > 10080) return 'Choose an interval between 5 minutes and 7 days.'
    if (!validTimezone(timezone.trim())) return 'Choose a valid timezone, or use your device timezone.'
    if (enabled && issues.length) return `Finish connecting your coach first: ${issues.join('; ')}.`
    if (enabled && state.settings.paused) return 'Your coach is paused. Resume your coach before turning on automatic check-ins.'
    return ''
  }
  return <div className="page-stack">
    <SectionHeading eyebrow="A little help, on your schedule" title="When should your coach check in?"
      action={<Badge tone={state.schedule.enabled ? 'green' : 'neutral'}>{state.schedule.enabled ? 'Automatic check-ins on' : 'Only when you ask'}</Badge>}>
      You choose the rhythm. Your saved rules apply to every team review.
    </SectionHeading>
    <div className="grid two"><section className="card">
      <label className="checkbox-label"><input type="checkbox" checked={enabled} disabled={!enabled && issues.length > 0}
        onChange={event => setEnabled(event.target.checked)} />Let my coach check in automatically</label>
      <p className="field-hint">Off means the coach only reviews your team when you ask.</p>
      <p className="field-hint">Current draft frequency: {frequency.toLowerCase()}. Nothing changes until you review and save.</p>
      <div className="choice-card-list">{choices.map(choice => <label className={`choice-card ${Number(interval) === choice.minutes ? 'selected' : ''}`} key={choice.minutes}>
        <input type="radio" name="check-in-frequency" checked={Number(interval) === choice.minutes} onChange={() => setInterval(String(choice.minutes))} />
        <span><strong>{choice.title}</strong><small>{choice.help}</small></span>
      </label>)}</div>
      <details className="technical-details"><summary>Custom timing and timezone</summary>
        <label htmlFor="interval">Minutes between reviews</label><input id="interval" type="number" min={5} max={10080} value={interval} onChange={event => setInterval(event.target.value)} />
        <label htmlFor="timezone">Timezone</label><input id="timezone" value={timezone} onChange={event => setTimezone(event.target.value)} />
        <button className="button text" onClick={() => setTimezone(Intl.DateTimeFormat().resolvedOptions().timeZone)}>Use this device's timezone</button>
      </details>
      <button className="button primary" onClick={() => {
        const error = validate()
        op.setError(error)
        if (!error) setReview(true)
      }}>Review schedule</button><OperationStatus {...op} />
    </section><div className="page-stack">
      <section className="card"><p className="eyebrow">Your next check-in</p><h3>{state.schedule.enabled ? dateTime(state.schedule.next_run_at, state.schedule.timezone) : 'Whenever you ask'}</h3>
        <p className="muted">{state.schedule.timezone}</p>
        {state.settings.paused && <Notice tone="info" message="Your coach is paused. Automatic check-ins cannot start while paused." />}
        {state.policy && <p className="field-hint">Your coach is limited to {state.policy.max_runs_per_day} reviews per day. Checks beyond that limit are skipped.</p>}
      </section>
      {issues.length > 0 && <section className="card"><h3>A quick setup step first</h3><ul className="checklist">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul></section>}
      <section className="card"><h3>Keep your clubhouse open</h3><p>Your computer needs to be awake, online, and running this app.</p>
        <p className="field-hint">Check-ins are spaced apart, not tied to a particular clock time. Restarting the app schedules the next check one interval ahead; missed reviews are not replayed. Your coach still cannot make ESPN moves.</p></section>
    </div></div>
    {review && <Modal title="Save this check-in schedule?" onClose={() => { if (!op.pending) setReview(false) }} footer={<>
      <button className="button secondary" disabled={op.pending} onClick={() => setReview(false)}>Go back</button>
      <button className="button primary" disabled={op.pending} onClick={() => {
        void op.perform(async () => {
          const error = validate()
          if (error) throw new Error(error)
          await request('/schedule', 'POST', { enabled, timezone: timezone.trim(), interval_minutes: Number(interval) })
          await onSaved()
          setReview(false)
        }, 'Check-in schedule saved.')
      }}>{op.pending ? 'Saving...' : 'Save schedule'}</button></>}>
      <p><strong>{enabled ? `Your coach will check in ${frequency.toLowerCase()}.` : 'Automatic check-ins will be off.'}</strong></p>
      <p>{enabled ? 'Each review uses your AI provider and may incur API charges. Your existing permissions and daily review limit still apply.' : 'You can still ask for a review from My team.'}</p>
      <p>No ESPN roster changes will be made.</p><OperationStatus {...op} />
    </Modal>}
  </div>
}
