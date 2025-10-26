以下は、**Zoom / Google Meet でエスペラント（Esperanto）会話を“常時・高精度・低遅延”に文字起こし**するための実現案を、最新の情報に基づいて比較・設計したものです。結論→**実運用の第一候補は Speechmatics（公式に「eo＝エスペラント」対応、リアルタイム・話者分離・カスタム辞書あり）**をコアにし、Zoom では「サードパーティ字幕 API」で Zoom 画面内に字幕を注入、Google Meet では **Meet Media API（開発者プレビュー）**が使えればそれで音声を取得、だめなら**サブウィンドウ/拡張で字幕を重ねる**、という構成が堅実です。根拠と手順を順に示します。

---

## 1) どの ASR（自動音声認識）エンジンを選ぶべきか

### A. クラウド：**Speechmatics**（推奨）

* **エスペラント対応を公式明記**（言語コード `eo`）。同ページで Interlingua などと並び “Esperanto eo” がリスト化。([docs.speechmatics.com][1])
* **リアルタイム STT と話者分離（diarization）**が提供され、**最終転写が 1 秒未満**とアピール（低遅延）。([Speechmatics][2])
* **リアルタイム話者分離の使い方**が公式ドキュメント化。([docs.speechmatics.com][3])
* **カスタム辞書**で専門用語や固有名詞（例：組織名、人名、地名、エスペラント固有語）を強化可。([docs.speechmatics.com][4])
* 注意：**自動言語識別（LID）ではエスペラントは対象外**。必ず `language: "eo"` を明示指定してください。([docs.speechmatics.com][5])

> 使うべき理由：エスペラントを“公式に”サポートし、低遅延・話者分離・辞書強化がワンストップで揃うため。

---

### B. オンデバイス（オフライン可）：**Whisper 系（faster‑whisper / whisper.cpp）**

* Whisper は多言語対応で、**エスペラントでの学習・微調整事例**が多数（HF の Esperanto ファインチューニング例）。([Hugging Face][6])
* **リアルタイム化の実装（Whisper‑Streaming）**も公開済み。GPU または高速 CPU で 1–2 秒級の実用レイテンシを狙えます。([GitHub][7])
* 長所：**プライバシー（端末内処理）**、インターネット不要、コスト固定。
* 短所：**高性能 GPU が欲しい**（大語彙・多言語で高精度を維持するには 12–16GB VRAM 以上推奨）、チューニング工数。

> 使うべき理由：クラウド持ち出しが難しい会議（機密・帯域制約）で、ローカル完結を優先する場合。

---

### C. 超軽量・完全ローカル：**Vosk（Kaldi 系）**

* **公式モデル一覧に Esperanto（`vosk-model-small-eo-0.42`）**があり、軽量でラズパイ級でも動作。([Alpha Cephei][8])
* 長所：非常に軽くオフライン、導入が簡単。
* 短所：**精度は Whisper/Speechmatics に劣る**（会議用途での誤認識が増えがち）。

> 使うべき理由：低スペック環境やバックアップ用の“保険”として。

---

### D. その他クラウド（Deepgram, Rev AI, Google, Azure 等）

* **Deepgram** は最新モデル（Nova‑3/Flux）で多言語・リアルタイム・話者分離・語彙強化（Keyterm/Keywords）を提供しますが、**エスペラントは公式対応リストに見当たりません**（多言語や “Whisper Cloud” はあるものの公式言語一覧に eo 記載なし）。([developers.deepgram.com][9])
* **Google Cloud Speech‑to‑Text / Azure Speech** の**公式対応言語一覧にエスペラントは現在掲載なし**。([Google Cloud][10])

> まとめ：**エスペラント前提なら Speechmatics か Whisper 系**が本命。

---

## 2) Zoom / Google Meet とのつなぎ込み

### Zoom

1. **Zoom に字幕を“注入”する方法（公式）**
   Zoom は**サードパーティ字幕 API（Closed Caption URL）**を提供。ホストが会議内で **API トークン付き URL をコピー**→任意の字幕システムから**HTTP POST（text/plain + `seq` 連番）**で字幕を送信すると、**Zoom の字幕 UI に表示**できます。([Zoom][11])

   > これにより、Speechmatics/Whisper で得たテキストを**Zoom の既定キャプション欄**にそのまま出せます（参加者は自分側でオン/サイズ変更可能）。

2. **Zoom から音声を取り出す**（ASR にかけるため）

   * **Meeting SDK の Raw Data**でミックス/個別の **PCM 16LE** を得られます（Windows/Mac 等の SDK）。([Zoom][12])
   * あるいは **ミーティングボット系サービス（例：Recall.ai）**で Zoom/Meet 双方の生メディアを統一 API で取得。構築負荷を回避できます。([Recall][13])

3. **Zoom の内蔵“翻訳字幕”は多数言語に対応**しますが、**エスペラントはリスト外**。本件では**外部 ASR→注入**が必要です。([Collab Support][14])

---

### Google Meet

* **Meet Media API（開発者プレビュー）**
  **会議のリアルタイム音声/映像にプログラムがアクセス**できる新 API。**自前の文字起こしや多言語字幕生成**などを想定しています（利用には **Developer Preview 登録**、**全参加者の同意/登録**が要件）。([Google for Developers][15])

  > 使えるなら：Media API で音声取得 → Speechmatics/Whisper へ → **Meet アドオン**や画面共有ウィンドウで字幕表示。
  > 使えない場合：**別ウィンドウ表示の字幕ビュー**（Web アプリ）を共有、あるいはブラウザ拡張で Meet の画面上にオーバーレイ。Meet の標準“翻訳字幕”機能はありますが **エスペラントは対象外**です。([Google Help][16])

* **Meet Add‑ons SDK**：会議内のサイドパネル/メインステージに**独自 UI を埋め込む**仕組み。字幕パネルを組み込みたい場合の器として有効。([Google for Developers][17])

---

## 3) 推奨アーキテクチャ（Zoom/Meet 共通）

### 構成図（概念）

**音声入力（Zoom/Meet）** →（Raw Data / Media API / 仮想オーディオで取得）→ **ASR（Speechmatics Realtime / Whisper）**
→（オプション）**話者分離・整形（句読点/大文字/記号/ŭ ĝ 等）／用語ブースト**
→ **表示**（Zoom には CC API で注入／Meet には Add‑on パネル or 共有ウィンドウ）

### 推奨設定（精度×遅延の妥協点）

* **フレーム/チャンク**：16kHz mono、20–40ms フレームで 500–1500ms ごとに送信。
* **VAD**（音声区間検出）：**Silero‑VAD** 等で話頭/話末を検出、**確定テキストは 0.8–1.5s 以内**を目標。([Genspark][18])
* **言語**：Speechmatics では **`language:"eo"` を固定指定**（LID は eo 非対応のため）。([docs.speechmatics.com][5])
* **話者分離**：会議では**リアルタイム diarization**を有効化（誰が話したかの理解が飛躍的に向上）。([docs.speechmatics.com][3])
* **カスタム辞書**：固有名詞・エスペラント固有語（**diakritika：ĉ ĝ ĥ ĵ ŝ ŭ**に注意）を**辞書/ブースト**に投入。([docs.speechmatics.com][4])

---

## 4) 最短レシピ（まず動かす）

### 4.1 Speechmatics（リアルタイム）＋Zoom 字幕注入

1. Speechmatics で API キーを取得し、**Realtime WebSocket**に `{"language":"eo","enable_partials":true,"diarization":true}` で接続。([docs.speechmatics.com][19])
2. Zoom をホストし、**[字幕]→[サードパーティ字幕を使用]→[API トークンをコピー]**。([Zoom][11])
3. Realtime で受け取る **確定テキスト**ごとに、Zoom の **Closed Caption URL**へ **`Content-Type: text/plain`** の **HTTP POST**（本文は字幕テキスト、クエリの `seq` は連番）。([Zoom][11])
4. 参加者は Zoom 標準のキャプション UI から表示・サイズ調整可能。

### 4.2 Google Meet の場合

* **Media API が使える**：Developer Preview 参加のうえ**リアルタイム音声を取得**→ ASR → Meet Add‑on サイドパネルで表示。([Google for Developers][15])
* **使えない**：ASR の結果を表示する**Web ページ**（大きな高コントラスト文字）を**画面共有**。または拡張/オーバーレイで重畳。Meet の内蔵“翻訳字幕”は **エスペラント対象外**。([Google Help][16])

### 4.3 Whisper（ローカル）構築の要点

* **Whisper‑Streaming 実装**を利用（CTranslate2/faster‑whisper でも可）。**VAD で区切り**、**自己適応的レイテンシ**で 1–2 秒遅延を目安に。([GitHub][7])
* Zoom 注入は Speechmatics と同じ **Closed Caption URL への POST**で可。([Zoom][11])

---

## 5) 品質を伸ばす実務 Tips

* **用語ブースト/辞書**

  * Speechmatics の **Custom Dictionary** に固有名詞や頻出語形（例：**“ĝenerala kunveno”, “kunlaboro”, “ĝi”, “ŝanco”** など）を投入。([docs.speechmatics.com][4])
  * Deepgram 系や Google 系を併用する場合は**Keywords/Keyterm**や**Speech Adaptation**で補強。([developers.deepgram.com][20])
* **記号/ダイアクリティカル**

  * 一部エンジンやキーボードで **x 方式（cx, gx, jx, sx, ux）** が混在することがあります。**後処理で x→記号 に正規化**して表示品質を揃えると読みやすい。
* **ノイズ対策**

  * 近接マイク＋ノイズ抑制（RNNoise など）→ VAD → ASR の順に。クロストーク時は **diarization** を前提に。([docs.speechmatics.com][3])
* **レイテンシ管理**

  * めやす：**中間結果 300–700ms、確定 0.8–1.5s**。Speechmatics は**「最終 <1s」**を公称。([Speechmatics][2])

---

## 6) 法務・運用（重要）

* **参加者の同意**：録音/転写/字幕注入を**必ず事前告知・同意**。Zoom/Meet いずれもプラットフォームのポリシーと組織の規定に従ってください。
* **Meet Media API** は**開発者プレビュー**で、**全参加者がプログラム参加**している必要があります。運用前に条件を確認。([Google for Developers][15])

---

## 7) 選定の最終指針（簡易まとめ）

| 目的/制約                                  | 最有力                                                                                                                                             |
| ------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **最高の安定性・低遅延・公式エスペラント** | **Speechmatics Realtime（`language:"eo"`、diarization+辞書）**。Zoom は CC API 注入、Meet は Media API or 共有で表示。([docs.speechmatics.com][1]) |
| **オフライン・機密・コスト固定**           | **Whisper（faster‑whisper / whisper‑streaming）** + Zoom CC 注入 or Meet 共有。([GitHub][7])                                                       |
| **軽量 / 低スペック**                      | **Vosk（`vosk-model-small-eo-0.42`）**（精度は要検証）。([Alpha Cephei][8])                                                                        |

---

## 8) ミニ実装ヒント（イメージ）

**Zoom へ字幕を送る HTTP POST（概念）**

* URL：`https://wmcc.zoom.us/closedcaption?...&seq=N`
* ヘッダ：`Content-Type: text/plain; charset=utf-8`
* ボディ：字幕テキスト（改行は `\n`）。**`seq` は成功ごとに +1**。([Zoom][11])

**Speechmatics Realtime（概念）**

* WebSocket 接続 → 16kHz mono PCM を 0.5–1.0s チャンクで送信 → `partial` を画面に即表示、`final` で Zoom/Meet に反映。設定例：`{"language":"eo","diarization":true,"punctuate":true}`。([docs.speechmatics.com][19])

---

## 9) 次にやること（チェックリスト）

1. **どのプラットフォームで回すか決定**（まずは Speechmatics で PoC → Whisper を保険）。([docs.speechmatics.com][1])
2. **Zoom で CC API 注入の疎通**（手元の字幕文字列を POST）。([Zoom][11])
3. **Meet** は **Media API（使えれば）**で音声取得の PoC、無理なら**字幕ビュー共有**で開始。([Google for Developers][15])
4. **辞書**に人名/用語/団体名/地名（エスペラント形）を登録。([docs.speechmatics.com][4])
5. **実会話の 10–15 分テスト**で誤り分析 → 語彙/マイク/話者分離/区切り長のチューニング。

---

必要なら、**使っている OS / マイク / GPU の有無 / 会議参加者数**を教えてください。上記いずれのルートでも、**具体的な設定ファイル例（Speechmatics Realtime の JSON 例、Zoom 注入スクリプトの雛形、Whisper‑Streaming の起動パラメータなど）**をこちらで作成し、**最短で“実際に画面に字幕が出る”ところまで**落とし込みます。

[1]: https://docs.speechmatics.com/speech-to-text/languages "Languages & Models | Speechmatics Docs"
[2]: https://www.speechmatics.com/product/real-time?utm_source=chatgpt.com "Real-Time Speech-to-Text | Speechmatics"
[3]: https://docs.speechmatics.com/speech-to-text/realtime/realtime_diarization?utm_source=chatgpt.com "Realtime diarization - Speechmatics Docs"
[4]: https://docs.speechmatics.com/speech-to-text/features/custom-dictionary?utm_source=chatgpt.com "Custom Dictionary - Speechmatics Docs"
[5]: https://docs.speechmatics.com/speech-to-text/batch/language-identification?utm_source=chatgpt.com "Language Identification (SaaS) - Speechmatics Docs"
[6]: https://huggingface.co/tarob0ba/whisper-small-eo?utm_source=chatgpt.com "tarob0ba/whisper-small-eo · Hugging Face"
[7]: https://github.com/ufal/whisper_streaming?utm_source=chatgpt.com "GitHub - ufal/whisper_streaming: Whisper realtime streaming ..."
[8]: https://alphacephei.com/vosk/models "VOSK Models"
[9]: https://developers.deepgram.com/docs/models-languages-overview "Models & Languages Overview | Deepgram's Docs"
[10]: https://cloud.google.com/speech-to-text/docs/speech-to-text-supported-languages "Speech-to-Text supported languages  |  Google Cloud"
[11]: https://support.zoom.com/hc/ja/article?id=zm_kb&sysparm_article=KB0060372&utm_source=chatgpt.com "サードパーティの字幕サービスの使用 - Zoom"
[12]: https://developers.zoom.us/docs/meeting-sdk/windows/add-features/raw-data/?utm_source=chatgpt.com "Use raw data | Meeting SDK | Windows"
[13]: https://www.recall.ai/blog/how-to-access-audio-streams-in-the-zoom-web-sdk?utm_source=chatgpt.com "How to access audio streams in the Zoom Web SDK?"
[14]: https://collab-support.sojitz-ti.com/hc/ja/articles/5491390160026-%E7%BF%BB%E8%A8%B3%E5%AD%97%E5%B9%95%E3%81%AE%E6%9C%89%E5%8A%B9%E5%8C%96-%E7%AE%A1%E7%90%86%E8%80%85%E8%A8%AD%E5%AE%9A?utm_source=chatgpt.com "翻訳字幕の有効化_管理者設定 – Zoom-Support"
[15]: https://developers.google.com/workspace/meet/media-api/guides/overview?utm_source=chatgpt.com "Meet Media API overview | Google Meet | Google for Developers"
[16]: https://support.google.com/meet/answer/10964115?co=GENIE.Platform%3DDesktop&hl=ja&utm_source=chatgpt.com "Google Meet で字幕の翻訳機能を利用する"
[17]: https://developers.google.com/workspace/meet/add-ons/guides/overview?hl=ja&utm_source=chatgpt.com "Meet アドオン SDK for Web の概要 - Google Developers"
[18]: https://www.genspark.ai/spark/whisper%E3%81%AB%E3%81%8A%E3%81%91%E3%82%8Bvad%E8%A8%AD%E5%AE%9A%E3%81%AE%E6%A6%82%E8%A6%81/c32b6cd5-7912-49d8-a993-c704a9cc8a25?utm_source=chatgpt.com "WhisperにおけるVAD設定の概要 - Genspark"
[19]: https://docs.speechmatics.com/speech-to-text/realtime/quickstart?utm_source=chatgpt.com "Quickstart - Speechmatics Docs"
[20]: https://developers.deepgram.com/docs/keyterm?utm_source=chatgpt.com "Keyterm Prompting | Deepgram's Docs"
