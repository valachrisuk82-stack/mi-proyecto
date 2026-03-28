par = input("Par de divisas (ej:EURUSD): ")
direccion = input("Direccion (compra/venta): ").lower()
entrada = float(input("Precio de entrada: "))
saldo = float(input("Saldo de tu cuenta ($): "))
pips_sl = float(input("Pips de Stop Loss: "))
pip = 0.0001
pips_tp = pips_sl * 2
riesgo_dinero = saldo * 0.02
if direccion == "compra":
    sl = round(entrada - pips_sl * pip, 5)
    tp = round(entrada + pips_tp * pip, 5)
else:
    sl = round(entrada + pips_sl * pip, 5)
    tp = round(entrada - pips_tp * pip, 5)
valor_pip = 10
lote = round(riesgo_dinero / (pips_sl * valor_pip), 2)
print(f"VALORES PARA MT5")
print(f"Stop Loss: {sl}")
print(f"Take Profit: {tp}")
print(f"Lote sugerido: {lote}")
