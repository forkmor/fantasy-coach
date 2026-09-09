import type { Player } from './players'

export function withPlayerNames(text: string, players: Player[]) {
  const names = new Map(players.map(player => [String(player.player_id), player.name]))
  return text
    .replace(/`(-?\d+)`/g, (match, id: string) => names.get(id) ?? match)
    .replace(/\bplayer(?:\s+id)?\s*[:#]\s*(-?\d+)\b/gi, (match, id: string) => names.get(id) ?? match)
}

export function reviewStatus(status: string) {
  return ({ completed: 'Ready to read', running: 'Your coach is working', queued: 'Starting soon',
    cancelling: 'Stopping safely', failed: 'Review interrupted', cancelled: 'Stopped',
    interrupted: 'Stopped when the app restarted' } as Record<string, string>)[status] ?? 'Status unavailable'
}

export function friendlyExcerpt(text: string, players: Player[]) {
  const usefulLines = withPlayerNames(text, players).split(/\n+/).map(line => line.trim())
    .filter(line => line && !/^sources?:/i.test(line))
  return usefulLines.join(' ')
    .replace(/`?(get_team|get_policy|get_transactions|search_available_players|get_action_status|plan_action|execute_action)`?/gi, 'the team review')
    .replace(/`?set_lineup`?/gi, 'lineup suggestions')
    .replace(/owner policy allows/gi, 'your coach settings allow')
    .replace(/owner policy/gi, 'your coach settings')
    .replace(/ESPN writes?/gi, 'ESPN changes')
    .replace(/authenticated ESPN write contract has not been verified/gi, 'real ESPN changes are not available yet')
    .replace(/\bdry[- ]run\b/gi, 'advice-only review')
    .replace(/[`*_#]/g, '')
    .replace(/\s+/g, ' ')
    .slice(0, 420)
}

export default function AdviceText({ text, players = [] }: { text: string; players?: Player[] }) {
  return <div className="coach-advice">{withPlayerNames(text, players).split(/\n+/).filter(line => line.trim()).map((line, index) => {
    const content = line.replace(/^#{1,6}\s+/, '').replace(/^\s*[-*]\s+/, '• ')
    const pieces = content.split(/(\*\*[^*]+\*\*)/g).map((part, piece) =>
      part.startsWith('**') && part.endsWith('**') ? <strong key={piece}>{part.slice(2, -2)}</strong> : part)
    return line.startsWith('#') ? <h3 key={index}>{pieces}</h3> : <p key={index}>{pieces}</p>
  })}</div>
}
