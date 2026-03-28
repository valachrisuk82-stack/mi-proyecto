saldo_cuenta = float(input("¿Cuál es tu saldo actual? $"))
interes_mes = float(input("¿Cuánto interés generó este mes? $"))

debito = round(interes_mes * 0.01, 2)

saldo_cuenta = saldo_cuenta - debito
total_ahorrado = debito

print(f"\nDébito realizado: ${debito}")
print(f"Saldo actualizado: ${saldo_cuenta}")
print(f"Total ahorrado: ${total_ahorrado}")