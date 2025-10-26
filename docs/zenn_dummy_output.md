# GNOMEで出力先をHDMIに変えただけで音が消える問題を追い詰めた話

## TL;DR
- GNOMEのクイック設定で「HDMI / DisplayPort – Built-in Audio」に切り替えると、PipeWire/WirePlumber が `Dummy Output` を既定シンクに固定し、以後アナログスピーカーへ戻しても音が鳴らなくなる状態になっていた。
- 原因は WirePlumber のキャッシュ (`~/.local/state/wireplumber/default-nodes` と `default-profile`) が HDMI プロファイルを既定に記憶したままになり、HDMI 側の ALSA PCM が開けず `auto_null` にフォールバック → その `auto_null` が「既定のシンク」として永続化される、という負のループだった。
- 対策として「キャッシュの初期化」「アナログを強制するスクリプト」と「state ファイルを監視して自動でアナログへ引き戻す systemd ユーザーユニット」を用意し、HDMI を誤タップしても 1〜2 秒で内蔵スピーカーへ復帰するようにした。

---

## 環境
- ハードウェア: Intel HDA (ALC293) + 1 本の HDMI モニター
- OS: Ubuntu 24.04 (GNOME + PipeWire 1.0.5 / WirePlumber 0.5 系)
- PipeWire 標準設定 (`/usr/share/wireplumber/main.lua.d/50-alsa-config.lua`) では `api.acp.auto-profile = false`、`api.acp.auto-port = false` になっている
- Zoom/Meet などでマイク・スピーカーを頻繁に切り替える運用

---

## 症状の再現
1. GNOME 右上のクイック設定から音声出力を「HDMI / DisplayPort – Built-in Audio」に変更
2. 即座に音が無音化。`wpctl status` を叩くとシンク一覧に `Dummy Output` しか存在しない
3. 再度「スピーカー – Built-in Audio」を選んでも戻らない。`pactl list sinks short` も `auto_null` のみ
4. `journalctl --user -u wireplumber` には `Error opening low-level control device 'hw:0'` や `Object activation aborted: proxy destroyed` が残る

---

## 何が起きていたか
PipeWire + WirePlumber は「最後にユーザーが選んだデバイス」を `~/.local/state/wireplumber/default-nodes` と `default-profile` に書き残す。  
しかし今回の環境では HDMI の PCM (`hdmi:0`) を開こうとした瞬間に ALSA レイヤーが `ENOENT` を返し、WirePlumber は安全策として `auto_null` (Dummy Output) を作ってしまう。このとき **失敗した HDMI/auto_null の組み合わせが再びキャッシュに書き戻され、次回セッション開始時から常に Dummy Output しか現れない** という悪循環が続いていた。

---

## 調査メモ
| コマンド | 目的 | 典型的な出力 |
| --- | --- | --- |
| `wpctl status` | PipeWire ノード一覧 | `Audio/Sink * 76. Dummy Output` |
| `pactl list short sinks` | PulseAudio 互換レイヤーのシンク | `auto_null` のみ |
| `cat ~/.local/state/wireplumber/default-nodes` | WirePlumber のキャッシュ内容 | `default.configured.audio.sink=auto_null` |
| `journalctl --user -u wireplumber -n 50` | ALSA エラーの検証 | `can't open control for card hw:0` |

これで「HDMI プロファイルを掴めず Dummy Output にフォールバックし、その状態がキャッシュされている」ことが確定した。

---

## 恒久対策

### 1. 一度キャッシュを初期化
```bash
mv ~/.local/state/wireplumber ~/.local/state/wireplumber.bak.$(date +%s)
systemctl --user restart wireplumber pipewire pipewire-pulse
```

### 2. 内蔵スピーカーを強制するスクリプト
`~/bin/wp-force-analog.sh`
```bash
#!/bin/bash
set -euo pipefail
sleep 2
pactl set-card-profile alsa_card.pci-0000_00_1f.3 output:analog-stereo+input:analog-stereo || true
pactl set-default-sink   alsa_output.pci-0000_00_1f.3.analog-stereo   || true
pactl set-default-source alsa_input.pci-0000_00_1f.3.analog-stereo   || true
```

### 3. systemd ユーザーユニットで自動実行
`~/.config/systemd/user/wp-force-analog.service`
```ini
[Unit]
Description=Force PipeWire default sink to built-in analog speakers
After=pipewire.service pipewire-pulse.service wireplumber.service
defaultDependencies=no

[Service]
Type=oneshot
ExecStart=%h/bin/wp-force-analog.sh
```

`~/.config/systemd/user/wp-force-analog.path`
```ini
[Unit]
Description=Monitor WirePlumber default nodes for unwanted HDMI switch

[Path]
PathChanged=%h/.local/state/wireplumber/default-nodes

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable --now wp-force-analog.service
systemctl --user enable --now wp-force-analog.path
```

効果:  
1. ログイン直後 (PipeWire 起動直後) に内蔵スピーカーを既定に設定  
2. GNOME で誤って HDMI を選択してキャッシュが書き換わっても、`.path` ユニットが即座にスクリプトを再実行し 1〜2 秒でアナログへ巻き戻す  

---

## 今後同じことを起こさないために
1. **HUD のトグルをむやみに触らない** — HDMI 出力を使う予定がないなら、WirePlumber の `main.lua.d` で該当ポートを `device.disabled = true` にするのも手。
2. **PipeWire/WirePlumber を更新** — 1.2 系では HDMI 失敗時のフォールバックが改善されているので、将来的には PPA でのアップデートも検討。
3. **状態ファイルを監視** — 今回追加した `.path` ユニットのように、自動化で“最後の砦”を用意しておくとヒューマンエラーで沈むことが無い。
4. **ログを即確認** — 音が出ない時は `wpctl status`, `pactl list short sinks`, `journalctl --user -u wireplumber` の 3 点をセットで確認する習慣をつける。

---

## まとめ
- 問題は「HDMI を選ぶ → ALSA が開けない → Dummy Output 化 → その状態を WirePlumber が永続化」という一連の流れだった。
- キャッシュの初期化とアナログ強制スクリプトにより、HDMI へ切り替えても自動でスピーカーへ戻す仕組みを構築。
- 将来的には PipeWire/WirePlumber のアップデート、および不要ポートの無効化を検討しつつ、今回の systemd ユニットを柵として維持することで再発防止が可能。

この手順を残しておけば、Zenn 記事としても「再現 → 原因 → 恒久対策」の筋立てで共有でき、他のメンバーが同じ沼にハマるのを防げます。
