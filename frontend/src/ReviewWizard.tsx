import { useState } from 'react'
import { errorMessage, request } from './api'
import Modal from './Modal'
import type { AppState, Run } from './types'
import { setupIssues } from './validation'
import { Notice, OperationStatus, useOperation } from './ui'

const GOALS = [
  { key: 'lineup', title: 'Set me up for this week', description: 'Check starters, injuries, and my bench.',
    objective: 'Review my current roster and recommend my best legal lineup for the current week. Check current injuries, projections, byes and locks where available. Use player names, explain each suggested change plainly, and explicitly call out missing information. Do not claim to execute any ESPN moves.' },
  { key: 'waivers', title: 'Find a helpful pickup', description: 'Look for available players without risking my keepers.',
    objective: 'Review my roster and available players for worthwhile pickups. Respect protected players and my waiver limits. Use player names and explain who each pickup would replace and why. State missing availability, waiver or budget data. Recommendations only; no ESPN moves.' },
  { key: 'trades', title: 'Take a fresh look at my roster', description: 'Find strengths, weak spots, and trade ideas.',
    objective: 'Review the strengths and weaknesses of my current roster and discuss trade ideas only within my owner policy. Use player names. Do not invent available offers or player values. Explain uncertainties and do not claim any trade was sent or accepted.' },
] as const

export default function ReviewWizard({ state, onSaved, onRun, onSetup, onPolicy, onClose }: {
  state: AppState; onSaved: () => Promise<void>; onRun: (id: string) => void
  onSetup: () => void; onPolicy: () => void; onClose: () => void
}) {
  const [step, setStep] = useState(0)
  const [goal, setGoal] = useState<(typeof GOALS)[number]['key']>('lineup')
  const [notes, setNotes] = useState('')
  const [resume, setResume] = useState(false)
  const op = useOperation()
  const issues = setupIssues(state)
  const chosen = GOALS.find(item => item.key === goal)!
  const unavailable = Boolean(state.active_run || state.espn_login?.active) || issues.length > 0
  return <Modal title={step === 0 ? 'What can your coach help with?' : 'Ready for your team review?'}
    onClose={() => { if (!op.pending) onClose() }} footer={
      <><button className="button secondary" disabled={op.pending} onClick={() => step ? setStep(0) : onClose()}>{step ? 'Back' : 'Not now'}</button>
        {step === 0 ? <button className="button primary" onClick={() => setStep(1)}>Next: review the plan</button>
          : <button className="button primary" disabled={op.pending || unavailable || (state.settings.paused && !resume)} onClick={() => {
            void op.perform(async () => {
              let resumed = false
              if (state.settings.paused) {
                await request('/settings', 'POST', { ...state.settings, paused: false })
                await onSaved()
                resumed = true
              }
              try {
                const run = await request<Run>('/runs', 'POST', { objective: `${chosen.objective}${notes.trim() ? `\nMy notes: ${notes.trim()}` : ''}` })
                await onSaved()
                onRun(run.id)
                onClose()
              } catch (error) {
                if (!resumed) throw error
                try {
                  await request('/pause', 'POST', {})
                  await onSaved()
                } catch (pauseError) {
                  throw new Error(`The review did not start, and the app could not confirm that your coach was paused again: ${errorMessage(pauseError)}`)
                }
                throw new Error(`The review did not start. Your coach was paused again, so automatic check-ins remain stopped. ${errorMessage(error)}`)
              }
            })
          }}>{op.pending ? 'Starting your review...' : 'Ask my coach'}</button>}</>
    }>
    <p className="step-indicator">Step {step + 1} of 2</p>
    {step === 0 ? <>
      <div className="choice-card-list">{GOALS.map(item => <label className={`choice-card ${goal === item.key ? 'selected' : ''}`} key={item.key}>
        <input type="radio" name="review-goal" value={item.key} checked={goal === item.key} onChange={() => setGoal(item.key)} />
        <span><strong>{item.title}</strong><small>{item.description}</small></span>
      </label>)}</div>
      <label htmlFor="coach-notes">Anything else your coach should know? <span className="muted">(optional)</span></label>
      <textarea id="coach-notes" rows={3} maxLength={2500} value={notes} onChange={event => setNotes(event.target.value)}
        placeholder="For example: I want to hold onto my starting running backs." />
    </> : <>
      <h3>{chosen.title}</h3><p>{chosen.description}</p>
      <ul className="review-checklist"><li>Use fresh ESPN roster information.</li><li>Follow your saved permissions and protect your keepers.</li>
        <li>Explain suggestions in plain English, using player names.</li><li>Leave all ESPN roster changes to you for now.</li></ul>
      <p className="field-hint">{state.settings.provider === 'grok' ? 'Grok' : 'Gemini'} receives football context and your notes. API usage may be charged by your provider. Do not include private information.</p>
      {state.settings.paused && <label className="checkbox-label resume-consent"><input type="checkbox" checked={resume} onChange={event => setResume(event.target.checked)} />
        <span>Resume my coach to run this review.<small>{state.schedule.enabled ? 'Your enabled scheduled reviews will also resume.' : 'Scheduled reviews remain off.'}</small></span></label>}
      {issues.length > 0 && <div className="setup-checks"><h3>A quick setup step first</h3><ul>{issues.map(issue => <li key={issue}>{issue}</li>)}</ul>
        <div className="button-row"><button className="button secondary" onClick={() => { onClose(); onSetup() }}>Open connections</button>
          {!state.policy && <button className="button secondary" onClick={() => { onClose(); onPolicy() }}>Choose coach settings</button>}</div></div>}
      {state.active_run && <Notice tone="info" message="Your coach is already reviewing the team. Wait for that review or stop it first." />}
      {state.espn_login?.active && <Notice tone="info" message="Finish signing in to ESPN before starting a review." />}
    </>}
    <OperationStatus {...op} />
  </Modal>
}
