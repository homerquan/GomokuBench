const SIZE = 600;
const START = 43;
const STEP = (SIZE - 2 * START) / 18;

function cell(move) {
  return move.move.split(",").map((value) => Number(value) - 1);
}

function drawGrid(ctx) {
  const wood = ctx.createLinearGradient(0, 0, SIZE, SIZE);
  wood.addColorStop(0, "#edc98a");
  wood.addColorStop(.56, "#dfad65");
  wood.addColorStop(1, "#dba356");
  ctx.fillStyle = wood;
  ctx.fillRect(0, 0, SIZE, SIZE);
  for (let grain = 0; grain < 180; grain++) {
    const y = grain * SIZE / 180;
    ctx.strokeStyle = grain % 5 === 0 ? "#7d471316" : "#fff4cd1a";
    ctx.lineWidth = grain % 5 === 0 ? 1.1 : .55;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.bezierCurveTo(177, y + (grain % 9 - 4), 416, y - grain % 5, SIZE, y + grain % 3);
    ctx.stroke();
  }
  ctx.font = "11px system-ui, sans-serif";
  ctx.textBaseline = "middle";
  ctx.textAlign = "center";
  ctx.fillStyle = "#5d4124";
  ctx.strokeStyle = "#6d4929";
  ctx.lineWidth = .9;
  ctx.beginPath();
  for (let index = 0; index < 19; index++) {
    const offset = START + index * STEP;
    ctx.moveTo(START, offset);
    ctx.lineTo(SIZE - START, offset);
    ctx.moveTo(offset, START);
    ctx.lineTo(offset, SIZE - START);
    ctx.fillText(index + 1, offset, 24);
    ctx.fillText(index + 1, offset, SIZE - 24);
    ctx.fillText(index + 1, 24, offset);
    ctx.fillText(index + 1, SIZE - 24, offset);
  }
  ctx.stroke();
  for (const x of [3, 9, 15]) {
    for (const y of [3, 9, 15]) {
      ctx.beginPath();
      ctx.arc(START + x * STEP, START + y * STEP, 2.7, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

function winningLine(moves, winner) {
  if (!moves.length || !winner) return [];
  const last = moves.at(-1);
  const [x, y] = cell(last);
  const stones = new Set(moves.filter((move) => move.color === winner).map((move) => cell(move).join(",")));
  for (const [dx, dy] of [[1, 0], [0, 1], [1, 1], [-1, 1]]) {
    const line = [last.move];
    for (const direction of [1, -1]) {
      let nx = x + dx * direction;
      let ny = y + dy * direction;
      while (stones.has([nx, ny].join(","))) {
        line.push(`${nx + 1},${ny + 1}`);
        nx += dx * direction;
        ny += dy * direction;
      }
    }
    if (line.length >= 5) return line;
  }
  return [];
}

export function renderBoard(canvas, moves, winner) {
  const ctx = canvas.getContext("2d");
  ctx.setTransform(canvas.width / SIZE, 0, 0, canvas.height / SIZE, 0, 0);
  drawGrid(ctx);
  const winners = new Set(winningLine(moves, winner));
  const radius = STEP * .435;
  moves.forEach((move, index) => {
    const [cx, cy] = cell(move);
    const x = START + cx * STEP;
    const y = START + cy * STEP;
    const white = move.color === "white";
    const shine = ctx.createRadialGradient(x - 4, y - 5, .5, x, y, radius);
    shine.addColorStop(0, white ? "#fffef8" : "#52575a");
    shine.addColorStop(.62, white ? "#f5f2e9" : "#24282b");
    shine.addColorStop(1, white ? "#d9d3c5" : "#0d1415");
    ctx.save();
    ctx.fillStyle = shine;
    ctx.shadowColor = "#503b236b";
    ctx.shadowBlur = 2.8;
    ctx.shadowOffsetY = 1.5;
    ctx.beginPath();
    ctx.arc(x, y, radius, 0, Math.PI * 2);
    ctx.fill();
    ctx.restore();
    if (winners.has(move.move) || index === moves.length - 1) {
      ctx.strokeStyle = winners.has(move.move) ? "#e7a21d" : (white ? "#8c521b" : "#ffcf85");
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.arc(x, y, radius * (winners.has(move.move) ? .89 : .33), 0, Math.PI * 2);
      ctx.stroke();
    }
  });
  const last = moves.at(-1);
  canvas.setAttribute("aria-label", last
    ? `Gomoku board after ${moves.length} moves. Last move ${last.color} ${last.move}.`
    : "Empty 19 by 19 Gomoku board.");
}
