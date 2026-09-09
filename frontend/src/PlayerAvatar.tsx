import { useState } from 'react'
import { safeImageUrl, type Player } from './players'

export default function PlayerAvatar({ player, large = false }: { player: Player; large?: boolean }) {
  const primary = safeImageUrl(player.headshot_url) ?? safeImageUrl(player.team_logo_url)
  const [failed, setFailed] = useState<string[]>([])
  const image = primary && !failed.includes(primary) ? primary : safeImageUrl(player.team_logo_url)
  const usable = image && !failed.includes(image) ? image : undefined
  return <span className={`player-avatar ${large ? 'large' : ''}`} aria-hidden="true">
    {usable ? <img src={usable} alt="" referrerPolicy="no-referrer" loading="lazy"
      onError={() => setFailed(current => [...current, usable])} />
      : <span className="player-initials">{player.position === 'D/ST' ? 'D/ST'
        : player.name.split(' ').map(word => word[0]).slice(0, 2).join('')}</span>}
  </span>
}
