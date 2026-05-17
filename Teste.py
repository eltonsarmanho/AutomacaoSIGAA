from lancamento_service import LancamentoService

svc = LancamentoService(
        matricula="202116040015",
        polo="CAMETÁ",
        periodo="2026.2",
        componente="ACC II",
    )

resultado = svc.matricular_sync()
if resultado.sucesso:
        print("Matrícula bem-sucedida!")
else:
        print("Erro na matrícula.")

resultado = svc.consolidar_sync(conceito="E")