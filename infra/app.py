import aws_cdk as cdk

from railpulse_infra.build import build_app

app = cdk.App()
build_app(app)
app.synth()
