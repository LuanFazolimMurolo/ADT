<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<title>Controle BTC</title>
<style>
    body {
        margin: 0;
        height: 100vh;
        display: flex;
        align-items: center;
        justify-content: center;
        background: #111;
    }
    button {
        font-size: 80px;
        width: 100%;
        height: 100%;
        border: none;
        color: white;
    }
</style>
</head>
<body>

<button id="btn">▶</button>

<script>
const SERVER = "";

async function sync() {
    const r = await fetch("/estado");
    const dados = await r.json();
    const estado = dados.botoes.btc;

    const btn = document.getElementById("btn");
    btn.textContent = estado ? "⏹" : "▶";
    btn.style.background = estado ? "#d32f2f" : "#2e7d32";
}

document.getElementById("btn").onclick = async () => {
    await fetch("/toggle/btc", { method: "POST" });
    sync();
};

setInterval(sync, 1000);
sync();
</script>

</body>
</html>
