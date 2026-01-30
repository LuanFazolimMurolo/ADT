import requests
import customtkinter as ctk

SERVER = "http://72.60.14.243:5000"

class App(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.geometry("400x400")
        self.estado = False

        self.btn = ctk.CTkButton(
            self, text="▶",
            font=("Arial", 48),
            command=self.toggle
        )
        self.btn.pack(expand=True, fill="both")

        self.sync()

    def ler_estado(self):
        r = requests.get(f"{SERVER}/estado", timeout=5)
        return r.json()

    def toggle(self):
        requests.post(f"{SERVER}/toggle/btc", timeout=5)
        self.sync()

    def sync(self):
        estado = self.ler_estado()

        novo = estado.get("botoes", {}).get("btc", False)

        if novo != self.estado:
            self.estado = novo
            self.btn.configure(
                text="⏹" if novo else "▶",
                fg_color="#d32f2f" if novo else "#2e7d32"
            )

        self.after(1000, self.sync)


if __name__ == "__main__":
    App().mainloop()
