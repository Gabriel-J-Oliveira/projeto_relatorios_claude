import csv
from pathlib import Path

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from django.utils.text import slugify

from relatorios.models import (
    OrigemSetorUsuario,
    PerfilUsuario,
    Setor,
    StatusImportacaoSetor,
    Tecnico,
    UsuarioSetorImportado,
    normalizar_nome_pessoa,
)
from relatorios.services.setores_service import (
    nome_tecnico_normalizado,
    nome_usuario_normalizado,
    sincronizar_setor_tecnico_por_usuario,
)


SAIDA_DIR = Path("imports") / "setores" / "saida"


class Command(BaseCommand):
    help = "Importa a lista oficial de setores/funcoes e aplica matches seguros em usuarios/tecnicos."

    def add_arguments(self, parser):
        parser.add_argument("arquivo_csv")
        parser.add_argument("--dry-run", action="store_true", help="Nao grava alteracoes.")
        parser.add_argument("--confirmar", action="store_true", help="Aplica alteracoes.")
        parser.add_argument("--sobrescrever", action="store_true", help="Sobrescreve setor ja confirmado.")
        parser.add_argument("--relatorio", action="store_true", help="Gera CSVs de saida.")
        parser.add_argument("--limite", type=int, default=0, help="Processa apenas as N primeiras linhas.")

    def handle(self, *args, **options):
        arquivo = Path(options["arquivo_csv"])
        if not arquivo.exists():
            raise CommandError(f"Arquivo nao encontrado: {arquivo}")
        confirmar = bool(options["confirmar"])
        dry_run = bool(options["dry_run"]) or not confirmar
        sobrescrever = bool(options["sobrescrever"])

        linhas = self._ler_csv(arquivo)
        limite = options.get("limite") or 0
        if limite:
            linhas = linhas[:limite]

        resultado = {
            "setores": [],
            "aplicados": [],
            "pendentes": [],
            "ambiguos": [],
            "resultado": [],
        }

        for linha_numero, linha in enumerate(linhas, start=2):
            item = self._normalizar_linha(linha)
            if not item["nome"] or not item["setor"]:
                resultado["pendentes"].append(
                    {
                        "linha": linha_numero,
                        "nome": item["nome"],
                        "setor": item["setor"],
                        "funcao": item["funcao"],
                        "observacao": "Linha sem nome ou setor.",
                    }
                )
                continue

            setor = self._obter_setor(item["setor"], dry_run)
            resultado["setores"].append({"setor": item["setor"], "acao": "validado" if dry_run else "criado/atualizado"})

            if not item["ativo"]:
                registro = {
                    "linha": linha_numero,
                    "nome": item["nome"],
                    "setor": item["setor"],
                    "funcao": item["funcao"],
                    "observacao": "Registro oficial marcado como inativo.",
                }
                resultado["pendentes"].append(registro)
                self._salvar_importado(item, setor, StatusImportacaoSetor.INATIVO, dry_run, registro["observacao"])
                continue

            nome_norm = normalizar_nome_pessoa(item["nome"])
            usuarios = self._usuarios_por_nome(nome_norm)
            tecnicos = self._tecnicos_por_nome(nome_norm)
            status = self._classificar_match(usuarios, tecnicos)

            if status == "ambiguo":
                registro = {
                    "linha": linha_numero,
                    "nome": item["nome"],
                    "setor": item["setor"],
                    "funcao": item["funcao"],
                    "usuarios": "; ".join(u.username for u in usuarios),
                    "tecnicos": "; ".join(t.nome for t in tecnicos),
                    "observacao": "Mais de um candidato encontrado.",
                }
                resultado["ambiguos"].append(registro)
                self._salvar_importado(item, setor, StatusImportacaoSetor.AMBIGUO, dry_run, registro["observacao"])
                continue

            if status == "pendente":
                registro = {
                    "linha": linha_numero,
                    "nome": item["nome"],
                    "setor": item["setor"],
                    "funcao": item["funcao"],
                    "observacao": "Usuario/tecnico ainda nao encontrado.",
                }
                resultado["pendentes"].append(registro)
                self._salvar_importado(item, setor, StatusImportacaoSetor.PENDENTE, dry_run, registro["observacao"])
                continue

            usuario = usuarios[0] if len(usuarios) == 1 else None
            tecnico = tecnicos[0] if len(tecnicos) == 1 else None
            aplicado = self._aplicar_match(item, setor, usuario, tecnico, dry_run, sobrescrever)
            resultado["aplicados"].append(
                {
                    "linha": linha_numero,
                    "nome": item["nome"],
                    "setor": item["setor"],
                    "funcao": item["funcao"],
                    "usuario": usuario.username if usuario else "",
                    "tecnico": tecnico.nome if tecnico else "",
                    "acao": aplicado,
                }
            )
            resultado["resultado"].append(
                {
                    "linha": linha_numero,
                    "nome": item["nome"],
                    "setor": item["setor"],
                    "funcao": item["funcao"],
                    "status": aplicado,
                }
            )

        if options["relatorio"]:
            self._gerar_relatorios(resultado)

        self.stdout.write(
            self.style.SUCCESS(
                "Importacao de setores concluida "
                f"({'dry-run' if dry_run else 'confirmada'}): "
                f"aplicados={len(resultado['aplicados'])}, "
                f"pendentes={len(resultado['pendentes'])}, "
                f"ambiguos={len(resultado['ambiguos'])}."
            )
        )

    def _ler_csv(self, arquivo):
        texto = arquivo.read_text(encoding="utf-8-sig")
        amostra = texto[:2048]
        dialect = csv.Sniffer().sniff(amostra, delimiters=",;")
        reader = csv.DictReader(texto.splitlines(), dialect=dialect)
        return list(reader)

    def _normalizar_linha(self, linha):
        mapa = {normalizar_nome_pessoa(k): v for k, v in (linha or {}).items()}
        ativo_txt = str(mapa.get("ativo", "sim") or "").strip().lower()
        return {
            "ativo": ativo_txt not in {"nao", "não", "n", "0", "false", "inativo"},
            "nome": str(mapa.get("nome", "") or "").strip(),
            "setor": str(mapa.get("setor", "") or "").strip(),
            "funcao": str(mapa.get("funcao", mapa.get("função", "")) or "").strip(),
        }

    def _obter_setor(self, nome, dry_run):
        slug_base = slugify(nome)[:130] or "setor"
        if dry_run:
            return Setor(nome=nome, slug=slug_base, ativo=True)
        setor = Setor.objects.filter(nome__iexact=nome).first()
        if not setor:
            slug = slug_base
            contador = 2
            while Setor.objects.filter(slug=slug).exists():
                slug = f"{slug_base[:125]}-{contador}"
                contador += 1
            setor = Setor.objects.create(nome=nome, slug=slug, ativo=True)
        if not setor.ativo:
            setor.ativo = True
            setor.save(update_fields=["ativo", "atualizado_em"])
        return setor

    def _usuarios_por_nome(self, nome_norm):
        User = get_user_model()
        return [u for u in User.objects.filter(is_active=True) if nome_usuario_normalizado(u) == nome_norm]

    def _tecnicos_por_nome(self, nome_norm):
        return [t for t in Tecnico.objects.filter(ativo=True) if nome_tecnico_normalizado(t) == nome_norm]

    def _classificar_match(self, usuarios, tecnicos):
        candidatos = len(usuarios) + len(tecnicos)
        if candidatos == 0:
            return "pendente"
        if len(usuarios) > 1 or len(tecnicos) > 1:
            return "ambiguo"
        return "aplicar"

    def _salvar_importado(self, item, setor, status, dry_run, observacao):
        if dry_run:
            return None
        importado, _criado = UsuarioSetorImportado.objects.update_or_create(
            nome_normalizado=normalizar_nome_pessoa(item["nome"]),
            defaults={
                "ativo": item["ativo"],
                "nome": item["nome"],
                "setor": setor,
                "funcao": item["funcao"],
                "status": status,
                "observacao": observacao,
            },
        )
        return importado

    def _aplicar_match(self, item, setor, usuario, tecnico, dry_run, sobrescrever):
        if dry_run:
            return "DRY_RUN_APLICARIA"
        agora = timezone.now()
        with transaction.atomic():
            importado, _criado = UsuarioSetorImportado.objects.update_or_create(
                nome_normalizado=normalizar_nome_pessoa(item["nome"]),
                defaults={
                    "ativo": item["ativo"],
                    "nome": item["nome"],
                    "setor": setor,
                    "funcao": item["funcao"],
                    "status": StatusImportacaoSetor.APLICADO,
                    "observacao": "Aplicado por importacao oficial.",
                    "usuario_vinculado": usuario,
                    "tecnico_vinculado": tecnico,
                    "aplicado_em": agora,
                },
            )
            if usuario:
                perfil, _ = PerfilUsuario.objects.get_or_create(usuario=usuario)
                if sobrescrever or not perfil.setor_confirmado:
                    perfil.setor = setor
                    perfil.funcao_setor = item["funcao"]
                    perfil.setor_confirmado = True
                    perfil.setor_origem = OrigemSetorUsuario.IMPORTACAO
                    perfil.setor_atualizado_em = agora
                    perfil.setor_atualizado_por = None
                    perfil.save(
                        update_fields=[
                            "setor",
                            "funcao_setor",
                            "setor_confirmado",
                            "setor_origem",
                            "setor_atualizado_em",
                            "setor_atualizado_por",
                            "atualizado_em",
                        ]
                    )
                    sincronizar_setor_tecnico_por_usuario(usuario, perfil, origem=OrigemSetorUsuario.IMPORTACAO)
                else:
                    return "JA_CONFIRMADO"
            if tecnico and (sobrescrever or not tecnico.setor_confirmado):
                tecnico.setor = setor
                tecnico.funcao_setor = item["funcao"]
                tecnico.setor_confirmado = True
                tecnico.setor_origem = OrigemSetorUsuario.IMPORTACAO
                tecnico.setor_atualizado_em = agora
                tecnico.setor_atualizado_por = None
                tecnico.save(
                    update_fields=[
                        "setor",
                        "funcao_setor",
                        "setor_confirmado",
                        "setor_origem",
                        "setor_atualizado_em",
                        "setor_atualizado_por",
                    ]
                )
            importado.save()
        return "APLICADO"

    def _gerar_relatorios(self, resultado):
        SAIDA_DIR.mkdir(parents=True, exist_ok=True)
        for nome, linhas in [
            ("setores_importados.csv", resultado["setores"]),
            ("usuarios_setor_aplicados.csv", resultado["aplicados"]),
            ("usuarios_setor_pendentes.csv", resultado["pendentes"]),
            ("usuarios_setor_ambiguos.csv", resultado["ambiguos"]),
            ("usuarios_setor_resultado.csv", resultado["resultado"]),
        ]:
            caminho = SAIDA_DIR / nome
            campos = sorted({k for linha in linhas for k in linha.keys()})
            with caminho.open("w", encoding="utf-8-sig", newline="") as arquivo:
                writer = csv.DictWriter(arquivo, fieldnames=campos or ["mensagem"], delimiter=";")
                writer.writeheader()
                if linhas:
                    writer.writerows(linhas)
                else:
                    writer.writerow({"mensagem": "Sem registros"})
