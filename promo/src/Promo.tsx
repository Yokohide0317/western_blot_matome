import React from 'react';
import { AbsoluteFill, Easing, Img, interpolate, Sequence, spring, staticFile, useCurrentFrame, useVideoConfig } from 'remotion';
import scenes from '../public/rec/scenes.json';

// Rendered on macOS: the system Japanese face keeps rendering offline and fast.
const fontFamily = '"Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans JP", sans-serif';

const C = {
  paper: '#f3f5f4',
  ink: '#14292e',
  muted: '#53656b',
  teal: '#286b6c',
  tealDeep: '#173f40',
  line: '#d5dedd',
  bubbleUser: '#286b6c',
  bubbleAi: '#ffffff',
};

const frames = Object.fromEntries(scenes.scenes.map((s) => [s.name, s.frames])) as Record<string, number>;

// Each recorded scene plays faster than real time so the whole film stays near 30 s.
const RATE = { membranes: 1.5, bands: 1.4, labels: 1.6, export: 1.3 } as const;
const recLength = (name: keyof typeof RATE) => Math.floor(frames[name] / RATE[name]);

const PLAN = [
  ['title', 84],
  ['chat', 150],
  ['membranes', recLength('membranes')],
  ['bands', recLength('bands')],
  ['labels', recLength('labels')],
  ['export', recLength('export')],
  ['figure', 96],
  ['end', 120],
] as const;
export const TOTAL = PLAN.reduce((sum, [, length]) => sum + length, 0);
const START = Object.fromEntries(
  PLAN.map(([name], i) => [name, PLAN.slice(0, i).reduce((sum, [, length]) => sum + length, 0)]),
) as Record<(typeof PLAN)[number][0], number>;
const LENGTH = Object.fromEntries(PLAN) as Record<(typeof PLAN)[number][0], number>;

const ease = Easing.bezier(0.16, 1, 0.3, 1);

// Fade in at the start and out at the end of a scene.
const useFade = (length: number, edge = 9) => {
  const frame = useCurrentFrame();
  return interpolate(frame, [0, edge, length - edge, length], [0, 1, 1, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
};

const rise = (frame: number, delay: number, distance = 24) => ({
  opacity: interpolate(frame - delay, [0, 14], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' }),
  transform: `translateY(${interpolate(frame - delay, [0, 18], [distance, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
    easing: ease,
  })}px)`,
});

// "WBM" first, then the rest of each word rises in and the name unfolds to "Western Blot Matome".
const WORDS = [['W', 'estern'], ['B', 'lot'], ['M', 'atome']] as const;

const Title: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const pop = spring({ frame, fps, config: { damping: 13, stiffness: 140 } });
  const unfold = 24;
  let order = 0;
  return (
    <AbsoluteFill style={{ background: C.paper, justifyContent: 'center', alignItems: 'center', opacity: useFade(LENGTH.title) }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 44 }}>
        <Img src={staticFile('shots/icon.png')} style={{ width: 168, height: 168, borderRadius: 38, transform: `scale(${0.6 + pop * 0.4})`, opacity: pop, boxShadow: '0 18px 40px rgba(20,41,46,.16)' }} />
        <div style={{ display: 'flex', fontSize: 112, fontWeight: 700, color: C.ink, letterSpacing: '-0.02em', lineHeight: 1.1, transform: `scale(${0.85 + pop * 0.15})`, opacity: pop }}>
          {WORDS.map(([head, rest], w) => (
            <span key={head} style={{ display: 'inline-flex', alignItems: 'baseline' }}>
              <span style={{ color: C.teal }}>{head}</span>
              {[...rest, ...(w < WORDS.length - 1 ? ['\u00a0'] : [])].map((ch, i) => {
                const t = frame - unfold - order++ * 1.1;
                const shown = interpolate(t, [0, 12], [0, 1], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease });
                return (
                  <span key={i} style={{ display: 'inline-block', overflow: 'hidden', maxWidth: `${shown * 1.1}em`, paddingBottom: '0.6em', marginBottom: '-0.6em',
                    opacity: shown, transform: `translateY(${(1 - shown) * 0.5}em)` }}>{ch}</span>
                );
              })}
            </span>
          ))}
        </div>
      </div>
    </AbsoluteFill>
  );
};

const typed = (text: string, frame: number, start: number, perFrame = 1.6) =>
  text.slice(0, Math.max(0, Math.floor((frame - start) * perFrame)));

const Bubble: React.FC<{ who: 'user' | 'ai'; children: React.ReactNode; style?: React.CSSProperties; size?: number }> = ({ who, children, style, size = 38 }) => (
  <div style={{ display: 'flex', flexDirection: 'column', alignItems: who === 'user' ? 'flex-end' : 'flex-start', gap: 10, ...style }}>
    <div style={{ fontSize: size * 0.58, fontWeight: 500, color: C.muted }}>{who === 'user' ? 'あなた' : 'Claude Code'}</div>
    <div style={{
      maxWidth: 1180, padding: `${size * 0.6}px ${size * 0.8}px`, borderRadius: 26, fontSize: size, lineHeight: 1.5, whiteSpace: 'pre-wrap',
      background: who === 'user' ? C.bubbleUser : C.bubbleAi, color: who === 'user' ? '#fff' : C.ink,
      border: who === 'user' ? 'none' : `2px solid ${C.line}`, boxShadow: '0 10px 30px rgba(20,41,46,.08)',
      borderBottomRightRadius: who === 'user' ? 8 : 26, borderBottomLeftRadius: who === 'user' ? 26 : 8,
    }}>{children}</div>
  </div>
);

const Chat: React.FC = () => {
  const frame = useCurrentFrame();
  const ask = '~/Desktop/WB_results/20271108_ChemiDoc_exported を\nウェスタンの図にまとめて';
  const reply = '膜を2枚見つけて、傾きをそろえました。\n画面を開きました → http://127.0.0.1:8765/p/…';
  return (
    <AbsoluteFill style={{ background: C.paper, opacity: useFade(LENGTH.chat), padding: '0 220px', justifyContent: 'center', gap: 56 }}>
      <div style={{ ...rise(frame, 0), fontSize: 46, fontWeight: 700, color: C.ink }}>Claude Code や Codex に、データを渡して頼むだけ。</div>
      <Bubble who="user" style={rise(frame, 6)}>{typed(ask, frame, 10, 1.5) || ' '}</Bubble>
      {frame > 64 && <Bubble who="ai" style={rise(frame, 64)}>{typed(reply, frame, 68, 2.2)}</Bubble>}
    </AbsoluteFill>
  );
};

const WINDOW = { width: 1536, top: 40 };

const Frames: React.FC<{ name: keyof typeof RATE }> = ({ name }) => {
  const frame = useCurrentFrame();
  const index = Math.min(frames[name] - 1, Math.floor(frame * RATE[name]));
  return <Img src={staticFile(`rec/${name}/${String(index).padStart(5, '0')}.jpg`)} style={{ width: '100%', display: 'block' }} />;
};

const Caption: React.FC<{ text: string; delay?: number; out?: number }> = ({ text, delay = 4, out }) => {
  const frame = useCurrentFrame();
  const leave = out === undefined ? 1 : interpolate(frame, [out - 8, out], [1, 0], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp' });
  return (
    <div style={{ position: 'absolute', left: 0, right: 0, bottom: 44, display: 'flex', justifyContent: 'center' }}>
      <div style={{ ...rise(frame, delay, 16), opacity: (rise(frame, delay).opacity as number) * leave, fontSize: 46, fontWeight: 700, color: C.ink, letterSpacing: '0.01em' }}>{text}</div>
    </div>
  );
};

const AppWindow: React.FC<{ name: keyof typeof RATE; children?: React.ReactNode; zoom?: [number, number, number, number] }> = ({ name, children, zoom }) => {
  const frame = useCurrentFrame();
  const length = recLength(name);
  // zoom = [scale, originX%, originY%, start frame]: a slow push-in on the part that matters.
  const scale = zoom ? interpolate(frame, [zoom[3], zoom[3] + 40], [1, zoom[0]], { extrapolateLeft: 'clamp', extrapolateRight: 'clamp', easing: ease }) : 1;
  return (
    <div style={{ position: 'absolute', left: (1920 - WINDOW.width) / 2, top: WINDOW.top, width: WINDOW.width, borderRadius: 18, overflow: 'hidden',
      background: '#fff', boxShadow: '0 30px 70px rgba(20,41,46,.18), 0 0 0 1px rgba(20,41,46,.08)' }}>
      <div style={{ height: 44, display: 'flex', alignItems: 'center', gap: 9, padding: '0 18px', background: '#e9eeed', borderBottom: `1px solid ${C.line}` }}>
        {['#d9dfde', '#d9dfde', '#d9dfde'].map((c, i) => <span key={i} style={{ width: 13, height: 13, borderRadius: 7, background: c }} />)}
        <div style={{ marginLeft: 18, flex: 1, height: 28, borderRadius: 8, background: '#fff', display: 'flex', alignItems: 'center', padding: '0 14px', fontSize: 17, color: C.muted }}>127.0.0.1:8765/p/…</div>
      </div>
      <div style={{ position: 'relative', overflow: 'hidden' }}>
        <div style={{ transform: `scale(${scale})`, transformOrigin: zoom ? `${zoom[1]}% ${zoom[2]}%` : 'center' }}>
          <Frames name={name} />
        </div>
        {children}
      </div>
      <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', opacity: 1 - useFade(length) }} />
    </div>
  );
};

const RecScene: React.FC<{ name: keyof typeof RATE; captions: [string, number, number?][]; zoom?: [number, number, number, number]; children?: React.ReactNode }> = ({ name, captions, zoom, children }) => (
  <AbsoluteFill style={{ background: C.paper, opacity: useFade(recLength(name), 7) }}>
    <AppWindow name={name} zoom={zoom}>{children}</AppWindow>
    {captions.map(([text, delay, out]) => <Caption key={text} text={text} delay={delay} out={out} />)}
  </AbsoluteFill>
);

const AgentNote: React.FC = () => {
  const frame = useCurrentFrame();
  const at = 46;
  return frame < at ? null : (
    <div style={{ position: 'absolute', right: 28, top: 92, ...rise(frame, at, 14) }}>
      <Bubble who="user" size={27}>2段目は β-actin、レーンは8本で</Bubble>
    </div>
  );
};

const Figure: React.FC = () => {
  const frame = useCurrentFrame();
  const scale = interpolate(frame, [0, LENGTH.figure], [1.0, 1.04]);
  return (
    <AbsoluteFill style={{ background: C.paper, opacity: useFade(LENGTH.figure), alignItems: 'center' }}>
      <div style={{ ...rise(frame, 0, 30), marginTop: 150, background: '#fff', borderRadius: 18, padding: '44px 56px', boxShadow: '0 30px 70px rgba(20,41,46,.14)' }}>
        <Img src={staticFile('shots/figure-crop.png')} style={{ width: 1500, display: 'block', transform: `scale(${scale})` }} />
      </div>
      <div style={{ ...rise(frame, 10), marginTop: 70, fontSize: 46, fontWeight: 700, color: C.ink, textAlign: 'center', lineHeight: 1.5 }}>
        煩わしいマーカーの位置調節も一緒に出力。
        <div style={{ fontSize: 34, fontWeight: 500, color: C.muted }}>PowerPointでそのまま編集できます</div>
      </div>
    </AbsoluteFill>
  );
};

const End: React.FC = () => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ background: C.tealDeep, justifyContent: 'center', alignItems: 'center', opacity: useFade(LENGTH.end, 10), color: '#fff' }}>
      <div style={{ ...rise(frame, 0), display: 'flex', alignItems: 'center', gap: 30 }}>
        <Img src={staticFile('shots/icon.png')} style={{ width: 104, height: 104, borderRadius: 24 }} />
        <div style={{ fontSize: 70, fontWeight: 700, letterSpacing: '-0.01em' }}>Western Blot Matome</div>
      </div>
      <div style={{ ...rise(frame, 10), marginTop: 64, fontSize: 40, fontWeight: 500, textAlign: 'center', lineHeight: 1.6 }}>
        ↓ 今すぐClaude Codeでインストール ↓
        <div style={{ marginTop: 18, display: 'inline-block', padding: '18px 34px', borderRadius: 16, background: 'rgba(255,255,255,.1)', fontSize: 34, color: '#d6efec' }}>
          「github.com/Yokohide0317/western_blot_matome のREADMEを読んで、導入して」
        </div>
      </div>
      <div style={{ ...rise(frame, 22), marginTop: 56, fontSize: 28, color: '#a9cbc8' }}>手元のPCで動作します（macOS・Windows）</div>
    </AbsoluteFill>
  );
};

export const Promo: React.FC = () => (
  <AbsoluteFill style={{ fontFamily, background: C.paper }}>
    <Sequence from={START.title} durationInFrames={LENGTH.title}><Title /></Sequence>
    <Sequence from={START.chat} durationInFrames={LENGTH.chat}><Chat /></Sequence>
    <Sequence from={START.membranes} durationInFrames={LENGTH.membranes}>
      <RecScene name="membranes" captions={[['メンブレンを自動検出。傾き補正も。', 4]]} />
    </Sequence>
    <Sequence from={START.bands} durationInFrames={LENGTH.bands}>
      <RecScene name="bands" captions={[['元画像を見ながら、切り出しを調整', 4, 104], ['露光を見比べて選ぶ。飽和は赤い点で警告', 108]]} />
    </Sequence>
    <Sequence from={START.labels} durationInFrames={LENGTH.labels}>
      <RecScene name="labels" captions={[['画面で直しても、AIに頼んでもOK！', 4]]} zoom={[1.3, 0, 22, 0]}><AgentNote /></RecScene>
    </Sequence>
    <Sequence from={START.export} durationInFrames={LENGTH.export}>
      <RecScene name="export" captions={[['ボタン1つで、PowerPointに書き出し', 4]]} />
    </Sequence>
    <Sequence from={START.figure} durationInFrames={LENGTH.figure}><Figure /></Sequence>
    <Sequence from={START.end} durationInFrames={LENGTH.end}><End /></Sequence>
  </AbsoluteFill>
);
