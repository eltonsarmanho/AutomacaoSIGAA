from lancamento_service import LancamentoService

svc = LancamentoService(
        matricula="202285940015",
        polo="OEIRAS DO PARÁ",
        periodo="2026.1",
        componente="ACC I",
    )

resultado = svc.matricular_sync()
if resultado.sucesso:
        print("Matrícula bem-sucedida!")
else:
        print("Erro na matrícula.")

resultado = svc.consolidar_sync(conceito="E")