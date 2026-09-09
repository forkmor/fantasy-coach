import { useEffect, useState } from 'react'
import { errorMessage, request } from './api'
import type { AppState, Run } from './types'
import { setupIssues } from './validation'
import Team from './Team'
import LeagueStats from './LeagueStats'
import ReviewWizard from './ReviewWizard'
import { friendlyExcerpt, reviewStatus } from './AdviceText'
import type { Player } from './players'
import { Badge, dateTime, Notice, OperationStatus, SectionHeading, useOperation } from './ui'

export default function Dashboard({ state, onSaved, onRun, onSetup, onPolicy }: {
  state: AppState; onSaved: () => Promise<void>; onRun: (id: string) => void; onSetup: () => void; onPolicy: () => void
}) {
  const [reviewOpen, setReviewOpen] = useState(false)
  const [latest, setLatest] = useState<Run | null>(null)
  const [historyError, setHistoryError] = useState('')
  const [players, setPlayers] = useState<Player[]>([])
  const cancelOp = useOperation()
  const issues = setupIssues(state)
  const connected = state.credentials.espn_s2 && state.credentials.swid
  useEffect(() => {
    let current = true
    void request<Run[]>('/runs').then(runs => { if (current) { setLatest(runs[0] ?? null); setHistoryError('') } })
      .catch(error => { if (current) setHistoryError(errorMessage(error)) })
    return () => { current = false }
  }, [state.active_run?.id, state.active_run?.status])
  return <div className="page-stack">
    <section className="welcome-panel fan-welcome">
      <div><p className="eyebrow">Your team. Your call.</p><h2>Less lineup stress.<br /><span>More game day.</span></h2>
        <p>Your coach brings the suggestions. You stay in charge of your squad.</p>
        <div className="button-row"><button className="button primary light-action" onClick={() => setReviewOpen(true)}
          disabled={Boolean(state.active_run)}>Review my team →</button>
          <button className="button welcome-secondary" onClick={onPolicy}>Adjust coach settings</button></div>
      </div><div className="field-art" aria-hidden="true"><div className="field-line one" /><div className="field-line two" /><div className="field-line three" />
        <span className="field-number left">20</span><span className="field-number right">20</span><div className="play-route" />
        <span className="player-dot first" /><span className="player-dot second" /><span className="player-dot third" /></div>
    </section>
    <div className="fan-status-strip">
      <span><i className={`status-dot ${state.settings.paused ? 'paused' : ''}`} />Coach {state.settings.paused ? 'paused' : 'ready'}</span>
      <span>{state.policy ? `${state.policy.protected_player_ids.length} keepers protected` : 'Choose your coach settings'}</span>
      <span>{state.schedule.enabled ? `Next check: ${dateTime(state.schedule.next_run_at, state.schedule.timezone)}` : 'Reviews when you ask'}</span>
      <Badge tone="blue">{state.settings.deep_research ? 'Deep research on' : 'Standard research'}</Badge>
    </div>
    {issues.length > 0 && <section className="card friendly-preflight">
      <SectionHeading title="Let's get your team ready">A couple of quick choices, then your coach can help.</SectionHeading>
      <ul className="checklist">{issues.map(issue => <li key={issue}>{issue}</li>)}</ul>
      <div className="button-row"><button className="button primary" onClick={onSetup}>Connect my accounts</button>
        {!state.policy && <button className="button secondary" onClick={onPolicy}>Choose my rules</button>}</div>
    </section>}
    {state.active_run && <section className="card active-review-card">
      <div><p className="eyebrow">Your coach is on it</p><h3>Reviewing your team...</h3><p className="muted">
        {state.settings.deep_research ? 'Checking every player’s ESPN news, injuries, practice notes, stats, and your preferences.'
          : 'Checking the facts and your preferences.'} Your ESPN roster is unchanged.</p></div>
      <div className="button-row"><button className="button primary" onClick={() => onRun(state.active_run!.id)}>Follow the review</button>
        <button className="button secondary" disabled={cancelOp.pending} onClick={() => {
          void cancelOp.perform(async () => { await request(`/runs/${state.active_run!.id}/cancel`, 'POST', {}); await onSaved() }, 'Team review stopped.')
        }}>Stop review</button></div><OperationStatus {...cancelOp} />
    </section>}
    <Team connected={connected} policy={state.policy} onSaved={onSaved} onPlayers={setPlayers} />
    <LeagueStats connected={connected} />
    <section className="card latest-review">
      <SectionHeading eyebrow="Your game plan" title="Last coach review" />
      {historyError && <Notice message={`Could not load your last review: ${historyError}`} />}
      {latest ? <><p className="field-hint">{dateTime(latest.started_at)} · {reviewStatus(latest.status)}</p>
        {latest.summary ? <p className="review-excerpt">{friendlyExcerpt(latest.summary, players)}</p>
          : latest.error ? <Notice message={`This review was interrupted: ${latest.error}`} />
            : <p className="review-excerpt">Open the review for progress and suggestions.</p>}
        <button className="button secondary" onClick={() => onRun(latest.id)}>Read my coach's advice →</button></>
        : !historyError && <><p>Your first review will appear here. Ask for lineup help, pickup ideas, or a fresh look at your roster.</p>
          <button className="button secondary" onClick={() => setReviewOpen(true)}>Choose a review</button></>}
    </section>
    <details className="technical-details"><summary>How recommendations work</summary>
      <p>Two-player lineup swaps and single free-agent adds or roster-player drops are verified live ESPN actions. You can confirm them directly, or explicitly enable Live mode so the coach may execute them within your saved rules. Waivers and trades remain disabled.</p>
      <p>Current assistant: {state.settings.provider === 'grok' ? 'Grok' : 'Gemini'}. Your saved limits apply to every review.</p>
    </details>
    {reviewOpen && <ReviewWizard state={state} onSaved={onSaved} onRun={onRun} onSetup={onSetup} onPolicy={onPolicy} onClose={() => setReviewOpen(false)} />}
  </div>
}
